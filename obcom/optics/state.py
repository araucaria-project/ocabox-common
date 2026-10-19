"""Proven state: what the solver is told about the world, and how a component's state axes are
resolved from it. Railway doctrine — *commanded is not proven*: an axis whose readback is
missing, moving, stale or maps to no declared symbol is undefined, and undefined validates
nothing. The only axis with a value nobody reported is a derived one (the dome, from shutter and
pointing); there are no defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping

from datamodels.optics import Environment, SelectorState, state_key

from obcom.optics.graph import Node, OpticalGraph
from obcom.optics.kinds import Axis


@dataclass(frozen=True)
class Hold:
    """A selector held by someone (the access grantor's knowledge): ``position`` is what it is held
    at, ``holder`` who holds it (free text shown to the operator). ``position=None`` holds a
    selector whose position is unknown (undefined readback): it must not move, so no route that
    needs it moved is feasible — and, unknown, it validates nothing either."""

    position: str | None
    holder: str | None = None


@dataclass(frozen=True)
class Declaration:
    """A position a person vouches for when no reading can prove it (the shutter was opened by
    hand while its sensor reports an error). It fills an otherwise undefined axis only — usable
    telemetry or derivation always wins — and stays visible as ``declared`` with ``by``."""

    position: str
    by: str


@dataclass(frozen=True)
class ProvenState:
    """Inputs of ``sees``/``check``: telemetry keyed by ``StateKey`` (``tertiary``,
    ``covercalibrator.calibrator``, ``thar_lamp``), the environment for derived axes, and the holds
    the grantor knows about. Immutable; build variants with :meth:`with_positions`.

    ``causes`` says, per state key, why the reader could not prove it — in words for people
    ("shutter reports an error (ShutterStatus 4)", "not read: TimeoutError"); an undefined axis
    without one gets the solver's own explanation (moving, stale, no telemetry …).

    ``declared`` are positions people vouch for (:class:`Declaration`): the last resort before
    undefined, never over a reading."""

    selectors: Mapping[str, SelectorState] = field(default_factory=dict)
    environment: Environment = field(default_factory=Environment)
    holds: Mapping[str, Hold] = field(default_factory=dict)
    causes: Mapping[str, str] = field(default_factory=dict)
    declared: Mapping[str, Declaration] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        positions: Mapping[str, str | None] | None = None,
        *,
        moving: Iterable[str] = (),
        stale: Iterable[str] = (),
        holds: Mapping[str, Hold] | None = None,
        causes: Mapping[str, str] | None = None,
        declared: Mapping[str, Declaration] | None = None,
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
        return cls(selectors=selectors, environment=Environment(**environment), holds=dict(holds or {}),
                   causes=dict(causes or {}), declared=dict(declared or {}))

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
    """The value of one axis, where it came from and, when undefined, why."""

    value: str | None  #: ``None`` = undefined
    origin: str  #: ``telemetry`` | ``derived`` | ``declared`` | ``undefined``
    cause: str | None = None  #: why it is undefined — or, when declared, why no reading proves it
    by: str | None = None  #: who declared it


def _unusable(telemetry: SelectorState, vocabulary: tuple[str, ...]) -> str | None:
    """Why a telemetry value proves nothing, ``None`` when it is usable."""
    if telemetry.stale:
        return "stale reading"
    if telemetry.moving:
        return "moving"
    if telemetry.position is None:
        return "reads no declared position"
    if telemetry.position not in vocabulary:
        return f"reads {telemetry.position!r}, not a declared position"
    return None


def resolve_axes(graph: OpticalGraph, state: ProvenState, node: Node) -> dict[str | None, Resolved]:
    """Every axis of ``node`` resolved against the proven state: telemetry wins on a reported axis
    (unusable ⇒ undefined), then derivation from the environment, then a person's declaration,
    else undefined. An undefined axis carries the reader's cause for its key, else the solver's own."""
    result: dict[str | None, Resolved] = {}
    for axis in node.axes:
        resolved = _resolve_axis(graph, state, node, axis)
        declaration = state.declared.get(state_key(node.name, axis.name))
        if resolved.value is None and declaration is not None and declaration.position in axis.vocabulary:
            resolved = Resolved(declaration.position, "declared", resolved.cause, declaration.by)
        result[axis.name] = resolved
    return result


def _resolve_axis(graph: OpticalGraph, state: ProvenState, node: Node, axis: Axis) -> Resolved:
    """One axis from what was read or derived — declarations aside."""
    key = state_key(node.name, axis.name)
    telemetry = state.selectors.get(key) if axis.reported else None
    if telemetry is not None:
        why = _unusable(telemetry, axis.vocabulary)
        return Resolved(telemetry.position, "telemetry") if why is None else Resolved(None, "undefined", state.causes.get(key, why))
    if axis.derive is not None:
        value = axis.derive(node.spec, graph.components, state.environment)
        if value is not None:
            return Resolved(value, "derived")
        why = axis.explain(node.spec, graph.components, state.environment) if axis.explain else "cannot be derived"
        return Resolved(None, "undefined", state.causes.get(key, why))
    return Resolved(None, "undefined", state.causes.get(key, "no telemetry"))


def axis_values(graph: OpticalGraph, state: ProvenState, node: Node) -> dict[str | None, str | None]:
    """The transfer-table row selector for ``node`` now."""
    return {name: r.value for name, r in resolve_axes(graph, state, node).items()}


def proven_position(graph: OpticalGraph, state: ProvenState, node: Node, aspect: str | None = None) -> str | None:
    """The proven symbol of one axis (the primary, or the named aspect); ``None`` = undefined."""
    resolved = resolve_axes(graph, state, node).get(aspect)
    return None if resolved is None else resolved.value
