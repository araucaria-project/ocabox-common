"""Proven state: what the solver is told about the world, and how a component's state axes are
resolved from it. Railway doctrine — *commanded is not proven*: an axis whose readback is
missing, moving, stale or maps to no declared symbol is undefined, and undefined validates
nothing. An axis nobody reports takes its kind's default (an aspect: ``off``; a lamp: lit), is
derived from the environment (the dome), or stays undefined (a selector's position).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Mapping

from datamodels.optics import Environment, SelectorState, state_key

from obcom.optics.graph import Node, OpticalGraph


class Unknown(Enum):
    """What ``proven_position`` returns instead of a symbol."""

    UNDEFINED = "undefined"  #: telemetry present but unusable, or nothing at all for an axis without a default
    ABSENT = "absent"  #: no telemetry; the axis runs on its default (nothing is *proven*)


@dataclass(frozen=True)
class Hold:
    """A selector held by someone (the access grantor's knowledge): ``position`` is what it is held
    at, ``holder`` who holds it (free text shown to the operator)."""

    position: str
    holder: str | None = None


@dataclass(frozen=True)
class ProvenState:
    """Inputs of ``sees``/``check``: telemetry keyed by ``StateKey`` (``tertiary``,
    ``covercalibrator.calibrator``, ``thar_lamp``), the environment for derived axes, and the holds
    the grantor knows about. Immutable; build variants with :meth:`with_positions`."""

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


@dataclass(frozen=True)
class Resolved:
    """The value of one axis and where it came from."""

    value: str | None  #: ``None`` = undefined
    origin: str  #: ``telemetry`` | ``derived`` | ``default`` | ``undefined``

    @property
    def assumed(self) -> bool:
        """Running on the kind's default: nothing was observed, so never report it as proven."""
        return self.origin == "default"


def resolve_axes(graph: OpticalGraph, state: ProvenState, node: Node) -> dict[str | None, Resolved]:
    """Every axis of ``node`` resolved against the proven state: telemetry wins (unusable ⇒
    undefined), then derivation from the environment, then the axis default, else undefined."""
    result: dict[str | None, Resolved] = {}
    for axis in node.axes:
        telemetry = state.selectors.get(state_key(node.name, axis.name))
        if telemetry is not None:
            usable = not (telemetry.stale or telemetry.moving or telemetry.position is None) and telemetry.position in axis.vocabulary
            result[axis.name] = Resolved(telemetry.position, "telemetry") if usable else Resolved(None, "undefined")
        elif axis.derive is not None:
            value = axis.derive(node.spec, graph.components, state.environment)
            result[axis.name] = Resolved(value, "derived") if value is not None else Resolved(None, "undefined")
        elif axis.default is not None:
            result[axis.name] = Resolved(axis.default, "default")
        else:
            result[axis.name] = Resolved(None, "undefined")
    return result


def axis_values(graph: OpticalGraph, state: ProvenState, node: Node) -> dict[str | None, str | None]:
    """The transfer-table row selector for ``node`` now."""
    return {name: r.value for name, r in resolve_axes(graph, state, node).items()}


def proven_position(graph: OpticalGraph, state: ProvenState, node: Node, aspect: str | None = None) -> str | Unknown:
    """The proven symbol of one axis (the primary, or the named aspect), or why not."""
    resolved = resolve_axes(graph, state, node).get(aspect)
    if resolved is None or resolved.value is None:
        return Unknown.UNDEFINED
    if resolved.assumed:
        return Unknown.ABSENT
    return resolved.value
