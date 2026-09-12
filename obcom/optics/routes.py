"""Static routes: every way light can reach a detector, as the axis values that make it so. No
runtime state — this is what ``compile`` tabulates, what ``check`` searches for a ``settable``
answer, and what load-time validation uses to prove every ``paths`` goal has at least one route.

The enumeration reads the same transfer tables as ``sees``, once per row (every combination of
the actuated axes). A row contributes a route only when it puts **exactly one** signal on the
port set the downstream hangs on: a cover open with its lamp on shows two things and is no
route at all, so *clean* routes (aspects off, no contamination) fall out of the table rather
than being a rule of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from datamodels.optics import UNDEFINED, GoalSpec, state_key

from obcom.optics.graph import Node, OpticalGraph
from obcom.optics.kinds import IN, Emit, Source, Transmit
from obcom.optics.sees import outgoing


@dataclass(frozen=True)
class StaticRoute:
    classes: frozenset[str]  #: every class this route can show (a stateful source contributes all its states)
    positions: Mapping[str, str]  #: StateKey → symbol, for a clean view
    terminal: str  #: the source, or the blocking/emitting selector
    via: tuple[str, ...]  #: components crossed terminal (excl.) → detector (excl.), in light direction

    def merged(self, more: Mapping[str, str], through: str | None) -> "StaticRoute | None":
        """Extend the upstream-computed route through ``through`` requiring ``more`` positions;
        ``None`` if the requirements contradict (the same axis needed twice in different values)."""
        positions = dict(self.positions)
        for key, symbol in more.items():
            if positions.get(key, symbol) != symbol:
                return None
            positions[key] = symbol
        via = self.via + (through,) if through is not None else self.via
        return StaticRoute(self.classes, positions, self.terminal, via)

    def moves_from(self, proven: Mapping[str, str | None]) -> dict[str, str]:
        """Positions that differ from ``proven`` (``None`` = unknown ⇒ must move)."""
        return {k: v for k, v in self.positions.items() if proven.get(k) != v}


def enumerate_routes(graph: OpticalGraph, detector: str) -> tuple[StaticRoute, ...]:
    """Every static route into ``detector``: table rows in vocabulary order, feeds in authored order."""
    return tuple(_arriving(graph, graph.node(detector), IN, {}))


def routes_for_goal(routes: Iterable[StaticRoute], goal: GoalSpec) -> tuple[StaticRoute, ...]:
    """Routes that show ``goal.see`` and honour ``goal.via`` (``via`` ⊆ route positions)."""
    return tuple(
        r for r in routes if goal.see in r.classes and all(r.positions.get(k) == v for k, v in goal.via.items())
    )


# --- internals ---------------------------------------------------------------------------------


def _arriving(graph: OpticalGraph, node: Node, in_port: str, memo) -> list[StaticRoute]:
    routes: list[StaticRoute] = []
    for feed in node.feeds.get(in_port, ()):
        routes.extend(_from(graph, graph.nodes[feed.upstream], feed.ports, memo))
    return routes


def _from(graph: OpticalGraph, node: Node, ports: frozenset[str], memo) -> tuple[StaticRoute, ...]:
    key = (node.name, ports)
    if key in memo:
        return memo[key]
    if isinstance(node.kind, Source):
        result: tuple[StaticRoute, ...] = (StaticRoute(node.kind.possible_classes(node.spec), {}, node.name, ()),)
    else:
        routes: list[StaticRoute] = []
        for values in node.assignments():
            signals = outgoing(node, values, ports)
            if len(signals) != 1:
                continue  # two things at once (light + lamp) is contamination, not a route
            (signal,) = signals
            positions = {state_key(node.name, axis): value for axis, value in values.items()}
            if isinstance(signal, Emit):
                if signal.light != UNDEFINED:
                    routes.append(StaticRoute(frozenset({signal.light}), positions, node.name, ()))
                continue
            assert isinstance(signal, Transmit)
            for upstream_route in _arriving(graph, node, signal.port, memo):
                merged = upstream_route.merged(positions, node.name)
                if merged is not None:
                    routes.append(merged)
        result = tuple(routes)
    memo[key] = result
    return result
