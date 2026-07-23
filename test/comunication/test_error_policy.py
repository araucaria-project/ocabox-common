"""Unit tests for the error_policy module.

Covers the value classes (Backoff, Budget, LogPolicy, SeverityRule,
ErrorPolicy) and presets. Cycle-query integration is tested separately
in test_cycle_query_error_policy.py.
"""

import time
import unittest

from obcom.comunication.error_policy import (
    Backoff,
    Budget,
    ErrorPolicy,
    LogPolicy,
    SeverityAction,
    SeverityRule,
    _LogPolicyState,
)
from obcom.data_colection.response_error import ResponseError


class TestBackoff(unittest.TestCase):
    def test_immediate_returns_zero(self):
        bo = Backoff.immediate()
        self.assertEqual(bo.delay(1), 0.0)
        self.assertEqual(bo.delay(100), 0.0)

    def test_fixed_returns_constant(self):
        bo = Backoff.fixed(2.5)
        self.assertEqual(bo.delay(1), 2.5)
        self.assertEqual(bo.delay(50), 2.5)

    def test_exponential_doubles_to_ceiling(self):
        bo = Backoff.exponential(initial=1.0, ceiling=10.0, factor=2.0)
        self.assertEqual(bo.delay(1), 1.0)   # 1
        self.assertEqual(bo.delay(2), 2.0)   # 1*2
        self.assertEqual(bo.delay(3), 4.0)   # 1*2*2
        self.assertEqual(bo.delay(4), 8.0)   # 1*2*2*2
        self.assertEqual(bo.delay(5), 10.0)  # capped
        self.assertEqual(bo.delay(20), 10.0)

    def test_exponential_no_overflow_at_very_large_attempt(self):
        # Regression test: attempt >1024 with factor=2.0 used to raise
        # OverflowError (2.0 ** 1024 overflows float) before the min()
        # clamp could fire.  After the fix it must silently return the
        # ceiling regardless of how large attempt is.
        bo = Backoff.exponential(initial=0.1, ceiling=5.0, factor=2.0)
        for attempt in (1025, 2000, 10_000, 100_000):
            result = bo.delay(attempt)   # must not raise
            self.assertEqual(result, 5.0,
                             f"attempt={attempt}: expected ceiling 5.0, got {result}")

    def test_staged_walks_through_stages(self):
        # 2s × 3, 10s × 5, then 60s forever
        bo = Backoff.staged([(2.0, 3), (10.0, 5), (60.0, None)])
        for n in (1, 2, 3):
            self.assertEqual(bo.delay(n), 2.0, f"attempt {n}: should be in fast stage")
        for n in (4, 5, 6, 7, 8):
            self.assertEqual(bo.delay(n), 10.0, f"attempt {n}: should be in medium stage")
        for n in (9, 100, 10_000):
            self.assertEqual(bo.delay(n), 60.0, f"attempt {n}: should be in tail stage")

    def test_staged_with_no_tail_keeps_last_delay(self):
        # Bounded stages without a None tail — past the budget, keep
        # using the last delay rather than crashing.
        bo = Backoff.staged([(1.0, 2), (5.0, 2)])
        self.assertEqual(bo.delay(1), 1.0)
        self.assertEqual(bo.delay(3), 5.0)
        self.assertEqual(bo.delay(4), 5.0)
        self.assertEqual(bo.delay(99), 5.0)


class TestBudget(unittest.TestCase):
    def test_unbounded_never_exhausted(self):
        b = Budget()
        self.assertFalse(b.is_exhausted(attempts=10**6, started_monotonic=0.0))

    def test_max_attempts(self):
        b = Budget(max_attempts=3)
        self.assertFalse(b.is_exhausted(attempts=2, started_monotonic=time.monotonic()))
        self.assertTrue(b.is_exhausted(attempts=3, started_monotonic=time.monotonic()))
        self.assertTrue(b.is_exhausted(attempts=4, started_monotonic=time.monotonic()))

    def test_max_seconds(self):
        b = Budget(max_seconds=0.05)
        start = time.monotonic()
        self.assertFalse(b.is_exhausted(attempts=1, started_monotonic=start))
        time.sleep(0.06)
        self.assertTrue(b.is_exhausted(attempts=1, started_monotonic=start))


class TestLogPolicy(unittest.TestCase):
    def test_first_n_then_throttled(self):
        # first_n=3, then 1 per 0.05s; check the boundary.
        policy = LogPolicy(first_n=3, then_every_seconds=0.05)
        state = policy.make_state()
        # First 3 calls all warn.
        self.assertTrue(state.should_warn())
        self.assertTrue(state.should_warn())
        self.assertTrue(state.should_warn())
        # 4th call inside the throttle window: suppressed.
        self.assertFalse(state.should_warn())
        # Wait past the throttle window: warns again.
        time.sleep(0.06)
        self.assertTrue(state.should_warn())
        # Subsequent call inside the new window is suppressed again.
        self.assertFalse(state.should_warn())

    def test_reset(self):
        policy = LogPolicy(first_n=1, then_every_seconds=3600.0)
        state = policy.make_state()
        self.assertTrue(state.should_warn())     # first warn
        self.assertFalse(state.should_warn())    # throttled
        state.reset()
        self.assertTrue(state.should_warn())     # loud again post-reset


class TestErrorPolicy(unittest.TestCase):
    def test_rule_for_known_severities(self):
        policy = ErrorPolicy.SERVICE
        self.assertIs(policy.rule_for(ResponseError.SEVERITY_TEMPORARY), policy.temporary)
        self.assertIs(policy.rule_for(ResponseError.SEVERITY_NORMAL), policy.normal)
        self.assertIs(policy.rule_for(ResponseError.SEVERITY_CRITICAL), policy.critical)

    def test_rule_for_unknown_falls_back_to_normal(self):
        # Code that doesn't carry an explicit severity (or carries an
        # unknown string) falls back to the NORMAL rule — same default
        # the response_error layer uses.
        policy = ErrorPolicy.SERVICE
        self.assertIs(policy.rule_for(None), policy.normal)
        self.assertIs(policy.rule_for('SOMETHING_NEW'), policy.normal)

    def test_with_overrides_replaces_only_named_rules(self):
        base = ErrorPolicy.SERVICE
        override = SeverityRule(action=SeverityAction.STOP)
        modified = base.with_overrides(normal=override)
        self.assertIs(modified.normal, override)
        # Untouched rules are the same instances.
        self.assertIs(modified.temporary, base.temporary)
        self.assertIs(modified.critical, base.critical)
        # Original is unchanged — SERVICE is a class-level singleton and
        # ``replace`` returns a new instance.
        self.assertEqual(base.normal.action, SeverityAction.RETRY)
        self.assertEqual(ErrorPolicy.SERVICE.normal.action, SeverityAction.RETRY)

    def test_interactive_preset_stops_on_normal(self):
        # Default: GUI-style. NORMAL/CRITICAL stop the subscription so
        # the human can react.
        policy = ErrorPolicy.INTERACTIVE
        self.assertEqual(policy.temporary.action, SeverityAction.RETRY)
        self.assertEqual(policy.normal.action, SeverityAction.STOP)
        self.assertEqual(policy.critical.action, SeverityAction.STOP)

    def test_service_preset_retries_normal(self):
        # Daemons: NORMAL is retried with backoff.
        policy = ErrorPolicy.SERVICE
        self.assertEqual(policy.temporary.action, SeverityAction.RETRY)
        self.assertEqual(policy.normal.action, SeverityAction.RETRY)
        self.assertEqual(policy.critical.action, SeverityAction.STOP)
        # NORMAL gets the staged backoff so quick blips recover fast
        # but multi-hour waits stay polite.
        delays = [policy.normal.backoff.delay(n) for n in (1, 4, 10)]
        self.assertEqual(delays, [2.0, 10.0, 60.0])

    def test_fail_fast_preset_has_bounded_temporary(self):
        policy = ErrorPolicy.FAIL_FAST
        self.assertIsNotNone(policy.temporary.budget)
        self.assertEqual(policy.temporary.budget.max_attempts, 3)
        self.assertEqual(policy.normal.action, SeverityAction.STOP)


if __name__ == '__main__':
    unittest.main()
