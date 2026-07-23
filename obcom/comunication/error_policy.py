"""Error policy for cycle queries / subscriptions.

A subscription's behaviour on each error severity is decided once at
subscribe time and then enforced by the library. The intent is that
clients should rarely need to write their own retry logic — they say what
they want at subscribe time and the cycle-query loop does the rest.

Three actions can be taken on an error:

* ``RETRY``  — library suppresses the error, sleeps the configured
  backoff, and rebuilds the subscription. The user callback never sees
  the error.
* ``NOTIFY`` — user callback fires with the error response *and* the
  library keeps retrying. Use when a downstream observer (operator UI,
  metrics) wants visibility into transient pain without owning the
  recovery loop.
* ``STOP``   — user callback fires once with the error response, then
  the cycle-query terminates. Same as the historical behaviour for
  non-TEMPORARY errors.

The default policy (``ErrorPolicy.INTERACTIVE``) keeps the historical
GUI-friendly behaviour: TEMPORARY is retried silently, NORMAL/CRITICAL
stop the subscription so the human can react. Long-running daemons opt
into ``ErrorPolicy.SERVICE``, which retries NORMAL with a graceful
backoff (fast at first, then slow) and throttles the resulting log
volume.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import ClassVar, List, Optional, Tuple

from obcom.data_colection.response_error import ResponseError

logger = logging.getLogger(__name__.rsplit('.', maxsplit=1)[-1])


class SeverityAction(str, Enum):
    """How the cycle-query should react to an error of a given severity."""

    RETRY = 'retry'
    NOTIFY = 'notify'
    STOP = 'stop'


# ---------------------------------------------------------------------------
# Backoff strategies
# ---------------------------------------------------------------------------

class Backoff:
    """Computes the delay between two consecutive retry attempts.

    Subclass instances are immutable and reusable across subscriptions.
    The cycle-query passes the per-subscription attempt counter (1-based)
    to :meth:`delay` and awaits the returned number of seconds.
    """

    def delay(self, attempt: int) -> float:
        raise NotImplementedError

    @classmethod
    def immediate(cls) -> 'Backoff':
        return _ImmediateBackoff()

    @classmethod
    def fixed(cls, seconds: float) -> 'Backoff':
        return _FixedBackoff(seconds=seconds)

    @classmethod
    def exponential(cls, initial: float, ceiling: float, factor: float = 2.0) -> 'Backoff':
        return _ExponentialBackoff(initial=initial, ceiling=ceiling, factor=factor)

    @classmethod
    def staged(cls, stages: List[Tuple[float, Optional[int]]]) -> 'Backoff':
        """Explicit staged schedule.

        Each stage is ``(delay_seconds, attempts)`` — apply ``delay`` for
        ``attempts`` consecutive retries, then move to the next stage. A
        final stage with ``attempts=None`` repeats forever at that delay.

        Example::

            Backoff.staged([(2.0, 3), (10.0, 5), (60.0, None)])

        meaning "two seconds for the first three attempts, ten seconds for
        the next five, then a minute apart indefinitely".
        """
        return _StagedBackoff(stages=tuple(stages))


@dataclass(frozen=True)
class _ImmediateBackoff(Backoff):
    def delay(self, attempt: int) -> float:  # noqa: ARG002 — unused
        return 0.0


@dataclass(frozen=True)
class _FixedBackoff(Backoff):
    seconds: float

    def delay(self, attempt: int) -> float:  # noqa: ARG002
        return float(self.seconds)


@dataclass(frozen=True)
class _ExponentialBackoff(Backoff):
    initial: float
    ceiling: float
    factor: float = 2.0

    def delay(self, attempt: int) -> float:
        # attempt is 1-based: first retry uses ``initial``, second uses
        # ``initial * factor``, etc., capped by ``ceiling``.
        if attempt < 1:
            attempt = 1
        # Guard against OverflowError when attempt is very large (e.g.
        # after thousands of retries during a multi-day outage).  At the
        # point where the un-clamped result would already exceed ceiling,
        # there is no need to exponentiate further — just return the
        # ceiling directly.
        try:
            raw = self.initial * (self.factor ** (attempt - 1))
        except OverflowError:
            return self.ceiling
        return min(raw, self.ceiling)


@dataclass(frozen=True)
class _StagedBackoff(Backoff):
    stages: Tuple[Tuple[float, Optional[int]], ...]

    def delay(self, attempt: int) -> float:
        # Walk the stages, decrementing ``attempt`` by each stage's
        # capacity. The final stage (capacity=None) absorbs the rest.
        remaining = max(attempt, 1)
        last_seconds = 0.0
        for seconds, capacity in self.stages:
            last_seconds = seconds
            if capacity is None:
                return seconds
            if remaining <= capacity:
                return seconds
            remaining -= capacity
        # All stages exhausted with bounded capacity — keep using the
        # last delay rather than crashing.
        return last_seconds


# ---------------------------------------------------------------------------
# Budget — how long are we willing to retry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Budget:
    """Bound on retry effort.

    A subscription whose retry budget is exhausted is treated as if its
    severity rule were ``STOP``: the user callback fires one final time
    with the error response and the cycle-query terminates.
    """

    max_attempts: Optional[int] = None    #: ``None`` = unbounded
    max_seconds: Optional[float] = None   #: wall-clock cap

    def is_exhausted(self, attempts: int, started_monotonic: float) -> bool:
        if self.max_attempts is not None and attempts >= self.max_attempts:
            return True
        if self.max_seconds is not None and (time.monotonic() - started_monotonic) >= self.max_seconds:
            return True
        return False


# ---------------------------------------------------------------------------
# Log throttling
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LogPolicy:
    """Rate-limit per-attempt retry warnings.

    The first ``first_n`` retries log at WARNING. After that, one
    WARNING per ``then_every_seconds`` of wall clock. DEBUG-level
    entries always fire so that forensic replay still has every
    attempt; only the journal-visible WARNINGs are throttled.
    """

    first_n: int = 1
    then_every_seconds: float = 3600.0

    def make_state(self) -> '_LogPolicyState':
        return _LogPolicyState(policy=self)


@dataclass
class _LogPolicyState:
    """Mutable per-subscription tracker for a :class:`LogPolicy`."""

    policy: LogPolicy
    fired: int = 0
    last_warned_at: float = -math.inf

    def should_warn(self) -> bool:
        self.fired += 1
        if self.fired <= self.policy.first_n:
            self.last_warned_at = time.monotonic()
            return True
        if (time.monotonic() - self.last_warned_at) >= self.policy.then_every_seconds:
            self.last_warned_at = time.monotonic()
            return True
        return False

    def reset(self) -> None:
        """Call when the subscription recovers, so the next failure logs loud again."""
        self.fired = 0
        self.last_warned_at = -math.inf


# ---------------------------------------------------------------------------
# Per-severity rule and full policy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeverityRule:
    """What to do for one severity level."""

    action: SeverityAction
    backoff: Backoff = field(default_factory=Backoff.immediate)
    budget: Optional[Budget] = None
    log: LogPolicy = field(default_factory=LogPolicy)


@dataclass(frozen=True)
class ErrorPolicy:
    """Bundle of per-severity rules attached to a subscription."""

    temporary: SeverityRule
    normal: SeverityRule
    critical: SeverityRule

    # Class-level presets — set after the class body so they are full
    # ``ErrorPolicy`` instances. See module bottom.
    INTERACTIVE: ClassVar['ErrorPolicy']
    SERVICE: ClassVar['ErrorPolicy']
    FAIL_FAST: ClassVar['ErrorPolicy']

    def with_overrides(self, *,
                       temporary: Optional[SeverityRule] = None,
                       normal: Optional[SeverityRule] = None,
                       critical: Optional[SeverityRule] = None) -> 'ErrorPolicy':
        """Return a copy with selected rules replaced."""
        kwargs = {}
        if temporary is not None:
            kwargs['temporary'] = temporary
        if normal is not None:
            kwargs['normal'] = normal
        if critical is not None:
            kwargs['critical'] = critical
        return replace(self, **kwargs)

    def rule_for(self, severity: Optional[str]) -> SeverityRule:
        """Look up the rule that applies to a given severity string.

        ``None`` and unknown severities fall back to the NORMAL rule —
        that's what historically happened with errors that didn't carry
        an explicit severity (``coded_error.py`` defaults to NORMAL too).
        """
        if severity == ResponseError.SEVERITY_TEMPORARY:
            return self.temporary
        if severity == ResponseError.SEVERITY_CRITICAL:
            return self.critical
        return self.normal


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

# Default — interactive clients (TOI, ocabox-cli, oca_monitor's direct
# subscriptions). NORMAL stops so the human notices and can decide
# whether to retry. TEMPORARY is suppressed quickly to mask transient
# zmq/router blips that the operator shouldn't have to care about.
ErrorPolicy.INTERACTIVE = ErrorPolicy(
    temporary=SeverityRule(
        action=SeverityAction.RETRY,
        backoff=Backoff.exponential(initial=0.1, ceiling=5.0),
    ),
    normal=SeverityRule(action=SeverityAction.STOP),
    critical=SeverityRule(action=SeverityAction.STOP),
)


# Long-running services — pms, ocabox-tcs daemons. Nobody is watching;
# the subscription has to nurse itself through "telescope is currently
# off, will be turned on tonight". The NORMAL backoff is staged so that
# a quick blip recovers fast, but a multi-hour wait doesn't generate one
# WARNING per minute.
ErrorPolicy.SERVICE = ErrorPolicy(
    temporary=SeverityRule(
        action=SeverityAction.RETRY,
        backoff=Backoff.exponential(initial=0.1, ceiling=5.0),
    ),
    normal=SeverityRule(
        action=SeverityAction.RETRY,
        backoff=Backoff.staged([(2.0, 3), (10.0, 6), (60.0, None)]),
        log=LogPolicy(first_n=3, then_every_seconds=3600.0),
    ),
    critical=SeverityRule(action=SeverityAction.STOP),
)


# Fail-fast — short-lived tools (CLI commands, tests). Short-budget
# retry on TEMPORARY only; everything else exits immediately.
ErrorPolicy.FAIL_FAST = ErrorPolicy(
    temporary=SeverityRule(
        action=SeverityAction.RETRY,
        backoff=Backoff.fixed(0.1),
        budget=Budget(max_attempts=3),
    ),
    normal=SeverityRule(action=SeverityAction.STOP),
    critical=SeverityRule(action=SeverityAction.STOP),
)


__all__ = [
    'Backoff',
    'Budget',
    'ErrorPolicy',
    'LogPolicy',
    'SeverityAction',
    'SeverityRule',
]
