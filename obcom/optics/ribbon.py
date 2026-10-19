"""The path ribbon: the optical path *to a detector*, as one line of components from the emitter
that feeds it now to the detector itself — what an instrument card draws.

The ribbon starts where the detector's light starts: the sky when it looks at the sky, the ThAr
lamp when BESO is switched to the lamp, the closed covers when they block it. A detector that sees
``dark`` or ``undefined`` still gets its whole way back to an emitter: past the blocking (or
unknown) component the ribbon goes on upstream, choosing at every switch

1. the input the switch is proven at (the light really comes that way, it just does not arrive);
2. else the input the ``target`` positions (the selected function's moves) would select;
3. else the first input in authored order,

so the operator sees what stands between the detector and the light, not a stump.

Built from the same transfer tables as :func:`obcom.optics.sees` and over any row source: a
:class:`ProvenState` in the service, the positions the service publishes (``self.selectors``) in a
client — :func:`published_values`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from itertools import product
from typing import Iterable, Mapping

from datamodels.optics import DARK, UNDEFINED, Archetype, SeesRecord, state_key

from obcom.optics.graph import Feed, Node, OpticalGraph
from obcom.optics.kinds import IN, Emit, Transmit
from obcom.optics.sees import ValuesOf, emerging, outgoing
from obcom.optics.state import ProvenState, axis_values


class StopRole(StrEnum):
    EMITTER = "emitter"  #: the light the detector sees starts here
    PASS = "pass"  #: on the detector's path, the light passes through
    BLOCKER = "blocker"  #: the detector sees dark because of this component
    UNKNOWN = "unknown"  #: the detector sees undefined because of this component
    UPSTREAM = "upstream"  #: beyond the blocker: on the way back to an emitter, its light does not arrive
    DETECTOR = "detector"


@dataclass(frozen=True)
class RibbonStop:
    name: str
    role: StopRole
    light: frozenset[str]  #: classes leaving towards the next stop (the detector: what it sees)

    @property
    def arrives(self) -> bool:
        """Its light reaches the detector (it is on the detector's current path)."""
        return self.role != StopRole.UPSTREAM


@dataclass(frozen=True)
class Ribbon:
    detector: str
    stops: tuple[RibbonStop, ...]  #: emitter side first, the detector last

    @property
    def terminal(self) -> RibbonStop:
        """Where what the detector sees starts: the emitter, the blocker or the unknown component."""
        return next(s for s in self.stops if s.role in (StopRole.EMITTER, StopRole.BLOCKER, StopRole.UNKNOWN))


def published_values(positions: Mapping[str, str | None]) -> ValuesOf:
    """Rows from resolved positions keyed by state key (``tertiary``, ``covercalibrator.calibrator``
    → symbol, ``None`` = undefined) — what the optics service publishes as ``self.selectors``."""
    return lambda node: {axis.name: positions.get(state_key(node.name, axis.name)) for axis in node.axes}


def seen_positions(graph: OpticalGraph, sees: Mapping[str, Iterable[SeesRecord]]) -> dict[str, str]:
    """Axis values read back from what the detectors see (detector → its ``sees`` records): along a
    record's way every component must pass the light on (the dome it came through from the sky is
    ``open``) and its terminal must emit what is seen (the sky seen as ``sky.flat`` is at
    ``flat``). Only what the tables decide unambiguously, and only where all records agree.

    For a client of a service that publishes ``sees`` but not every axis (optics < 1.5 publishes
    neither the sources nor the derived dome): the drawing must never contradict ``sees``."""
    found: dict[str, set[str]] = {}
    for detector, records in sees.items():
        for record in records:
            chain = [record.terminal, *record.via, detector]
            for i, name in enumerate(chain[:-1]):
                node = graph.nodes.get(name)
                down = graph.nodes.get(chain[i + 1])
                if node is None or down is None or not node.axes:
                    continue
                ports = next((f.ports for feeds in down.feeds.values() for f in feeds if f.upstream == name), None)
                if ports is None:
                    continue
                if i == 0:
                    if record.light_class == UNDEFINED:
                        continue  # an unknown terminal proves nothing about itself
                    expected = frozenset({Emit(record.light_class)})
                else:
                    port = next((p for p, feeds in node.feeds.items() if any(f.upstream == chain[i - 1] for f in feeds)), None)
                    if port is None:
                        continue
                    expected = frozenset({Transmit(port)})
                for axis, value in _decided(node, ports, expected).items():
                    found.setdefault(state_key(name, axis), set()).add(value)
    return {key: values.pop() for key, values in found.items() if len(values) == 1}


def _decided(node: Node, ports: frozenset[str], expected) -> dict[str | None, str]:
    """The axes whose value is the same in every row of ``node`` that puts exactly ``expected``
    towards ``ports``."""
    rows = [dict(zip((a.name for a in node.axes), combo)) for combo in product(*(a.vocabulary for a in node.axes))]
    matches = [values for values in rows if outgoing(node, values, ports) == expected]
    if not matches:
        return {}
    return {axis.name: matches[0][axis.name] for axis in node.axes if len({m[axis.name] for m in matches}) == 1}


def ribbon(graph: OpticalGraph, values_of: ValuesOf | ProvenState, detector: str,
           target: Mapping[str, str] | None = None) -> Ribbon:
    """The ribbon of ``detector`` now. ``target`` (state key → symbol) are the positions a selected
    function would move to; they only choose the way beyond a blocker, never what is lit."""
    if isinstance(values_of, ProvenState):
        values_of = partial(axis_values, graph, values_of)
    node = graph.node(detector)
    if node.archetype != Archetype.DETECTOR:
        raise ValueError(f"{detector!r} is a {node.archetype}, not a detector")
    targeted = _targeted(values_of, target or {})
    memo: dict = {}
    seen = frozenset(r.light_class for feed in node.feeds.get(IN, ()) for r in emerging(graph, values_of, feed.upstream, feed.ports, memo))
    stops = [RibbonStop(detector, StopRole.DETECTOR, seen or frozenset({DARK}))]
    arrives, in_port, current = True, IN, node
    while (feed := _feed(graph, values_of, current, in_port, memo)) is not None:
        up = graph.nodes[feed.upstream]
        light = frozenset(r.light_class for r in emerging(graph, values_of, up.name, feed.ports, memo))
        signals = outgoing(up, values_of(up), feed.ports)
        passing = next((s.port for s in signals if isinstance(s, Transmit)), None)
        if not arrives:
            role = StopRole.UPSTREAM
        elif passing is not None:
            role = StopRole.PASS
        else:
            emitted = {s.light for s in signals if isinstance(s, Emit)}
            role = (StopRole.EMITTER if emitted - {DARK, UNDEFINED}
                    else StopRole.UNKNOWN if UNDEFINED in emitted else StopRole.BLOCKER)
        stops.append(RibbonStop(up.name, role, light))
        if role == StopRole.EMITTER:
            break
        arrives = arrives and role == StopRole.PASS
        in_port = passing or _chosen(up, targeted, feed.ports)
        current = up
    return Ribbon(detector, tuple(reversed(stops)))


# --- internals ---------------------------------------------------------------------------------


def _targeted(values_of: ValuesOf, target: Mapping[str, str]) -> ValuesOf:
    def values(node: Node):
        values = dict(values_of(node))
        for axis in node.axes:
            values[axis.name] = target.get(state_key(node.name, axis.name), values[axis.name])
        return values
    return values


def _feed(graph: OpticalGraph, values_of: ValuesOf, node: Node, in_port: str | None, memo) -> Feed | None:
    """The feed of ``in_port`` the ribbon follows: the first that carries light, else the first."""
    feeds = node.feeds.get(in_port, ()) if in_port is not None else ()
    lit = (f for f in feeds if any(r.light_class not in (DARK, UNDEFINED) for r in emerging(graph, values_of, f.upstream, f.ports, memo)))
    return next(lit, feeds[0] if feeds else None)


def _chosen(node: Node, targeted: ValuesOf, ports: frozenset[str]) -> str | None:
    """The input beyond a component that passes nothing now: the one the target positions select,
    else the first authored one."""
    passing = next((s.port for s in outgoing(node, targeted(node), ports) if isinstance(s, Transmit)), None)
    return passing if passing is not None else next(iter(node.feeds), None)
