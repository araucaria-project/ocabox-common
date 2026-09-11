"""Proven state: what the solver is told about the world. Railway doctrine — *commanded is not
proven*: a selector whose readback is missing, moving, stale or maps to no declared symbol is
``undefined``, and ``undefined`` validates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Mapping

from datamodels.optics import Environment, SelectorState, state_key

from obcom.optics.graph import Node, OpticalGraph
from obcom.optics.kinds import Selector, Source


class Unknown(Enum):
    """What ``proven_position`` returns instead of a symbol."""

    UNDEFINED = "undefined"  #: telemetry present but unusable, or a selector without any telemetry
    ABSENT = "absent"  #: no telemetry for an *optional* axis (an aspect, a switchable source): nothing is asserted


@dataclass(frozen=True)
class Hold:
    """A selector held by someone (the access grantor's knowledge): ``position`` is what it is held
    at, ``holder`` who holds it (free text shown to the operator)."""

    position: str
    holder: str | None = None


@dataclass(frozen=True)
class ProvenState:
    """Inputs of ``sees``/``check``: proven selector states keyed by ``StateKey`` (``tertiary``,
    ``covercalibrator.calibrator``), the environment for derived selectors and stateful sources,
    and the holds the grantor knows about. Immutable; build variants with :meth:`with_positions`."""

    selectors: Mapping[str, SelectorState] = field(default_factory=dict)
    environment: Environment = field(default_factory=Environment)
    holds: Mapping[str, Hold] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        positions: Mapping[str, str | None] | None = None,
        *,
        moving: Iterable[str] = (),
        stale: Iterable[str] = (),
        holds: Mapping[str, Hold] | None = None,
        **environment,
    ) -> "ProvenState":
        """Convenience constructor: ``ProvenState.build({'tertiary': 'andor'}, sun_alt_deg=-30)``.
        A ``None`` position is an unmapped readback (oca-problems#107)."""
        moving, stale = set(moving), set(stale)
        selectors = {
            key: SelectorState(position=pos, moving=key in moving, stale=key in stale)
            for key, pos in (positions or {}).items()
        }
        for key in (moving | stale) - set(selectors):
            selectors[key] = SelectorState(position=None, moving=key in moving, stale=key in stale)
        return cls(selectors=selectors, environment=Environment(**environment), holds=dict(holds or {}))

    def with_positions(self, positions: Mapping[str, str | None]) -> "ProvenState":
        selectors = dict(self.selectors)
        for key, pos in positions.items():
            selectors[key] = SelectorState(position=pos)
        return replace(self, selectors=selectors)

    def with_environment(self, **environment) -> "ProvenState":
        merged = {**self.environment.model_dump(exclude_none=True), **environment}
        return replace(self, environment=Environment(**merged))


def proven_position(graph: OpticalGraph, state: ProvenState, node: Node, aspect: str | None = None) -> str | Unknown:
    """The proven symbol of a selector (or one aspect of it, or a switchable source), or why not.

    Explicit telemetry wins; a derived selector (dome) computes its position from the environment
    when it has none; a primary axis without any information is ``UNDEFINED``, an optional axis
    ``ABSENT``.
    """
    key = state_key(node.name, aspect)
    telemetry = state.selectors.get(key)
    vocabulary = _vocabulary(node, aspect)
    if telemetry is not None:
        if telemetry.stale or telemetry.moving or telemetry.position is None:
            return Unknown.UNDEFINED
        if vocabulary is not None and telemetry.position not in vocabulary:
            return Unknown.UNDEFINED  # unmapped readback — the M3 "2" case
        return telemetry.position
    if aspect is not None or isinstance(node.kind, Source):
        return Unknown.ABSENT
    if isinstance(node.kind, Selector):
        derived = node.kind.derived_position(node.spec, graph.components, state.environment)
        return Unknown.UNDEFINED if derived is None else derived
    return Unknown.ABSENT


def _vocabulary(node: Node, aspect: str | None) -> frozenset[str] | None:
    if aspect is not None:
        asp = node.kind.aspect(aspect) if isinstance(node.kind, Selector) else None
        return asp.positions if asp is not None else frozenset()
    if isinstance(node.kind, Source):
        return frozenset({"on", "off"})
    return node.positions
