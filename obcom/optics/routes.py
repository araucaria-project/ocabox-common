"""Static routes: every way light can reach a detector, as the selector positions that make it
so. No runtime state — this is what ``compile`` tabulates, what ``check`` searches for a
``settable`` answer, and what load-time validation uses to prove every ``paths`` goal has at
least one route.

A route is *clean*: it pins every selector on the path to the transmitting position **and** every
emitting aspect it crosses to ``off`` (a sky route through the cover calibrator requires the lamp
off), so that following it yields exactly the goal class and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from datamodels.optics import DARK, Archetype, GoalSpec, state_key

from obcom.optics.graph import Node, OpticalGraph
from obcom.optics.kinds import SelectorShape, Source


@dataclass(frozen=True)
class StaticRoute:
    classes: frozenset[str]  #: every class this route can show (a stateful source contributes all its states)
    positions: Mapping[str, str]  #: StateKey → symbol, for a clean view
    terminal: str  #: the source, or the blocking/emitting selector
    via: tuple[str, ...]  #: components crossed terminal (excl.) → detector (excl.), in light direction

    def merged(self, more: Mapping[str, str], through: str | None) -> "StaticRoute | None":
        """Extend upstream-computed route through ``through`` requiring ``more`` positions; ``None``
        if the requirements contradict (the same selector needed twice in different positions)."""
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
    """Every static route into ``detector``, in authored order."""
    node = graph.node(detector)
    memo: dict[tuple[str, str | None], tuple[StaticRoute, ...]] = {}
    routes: list[StaticRoute] = []
    for edge in node.inputs:
        routes.extend(_routes_out(graph, graph.nodes[edge.upstream], edge.upstream_port, memo))
    return tuple(routes)


def routes_for_goal(routes: Iterable[StaticRoute], goal: GoalSpec) -> tuple[StaticRoute, ...]:
    """Routes that show ``goal.see`` and honour ``goal.via`` (``via`` ⊆ route positions)."""
    return tuple(
        r for r in routes if goal.see in r.classes and all(r.positions.get(k) == v for k, v in goal.via.items())
    )


def _routes_out(graph: OpticalGraph, node: Node, port: str | None, memo) -> tuple[StaticRoute, ...]:
    key = (node.name, port)
    if key in memo:
        return memo[key]
    result = tuple(_compute_routes_out(graph, node, port, memo))
    memo[key] = result
    return result


def _inputs(graph: OpticalGraph, node: Node, memo) -> list[StaticRoute]:
    routes: list[StaticRoute] = []
    for edge in node.inputs:
        if edge.downstream_position is not None:
            continue  # fan-in inputs are handled per position by the selector itself
        routes.extend(_routes_out(graph, graph.nodes[edge.upstream], edge.upstream_port, memo))
    return routes


def _extend(routes: Iterable[StaticRoute], more: Mapping[str, str], through: str) -> list[StaticRoute]:
    out = []
    for r in routes:
        m = r.merged(more, through)
        if m is not None:
            out.append(m)
    return out


def _compute_routes_out(graph: OpticalGraph, node: Node, port: str | None, memo) -> list[StaticRoute]:
    if node.archetype == Archetype.SOURCE:
        assert isinstance(node.kind, Source)
        return [StaticRoute(node.kind.possible_classes(node.spec), {}, node.name, ())]
    if node.archetype in (Archetype.PASSIVE, Archetype.SPLITTER):
        return _extend(_inputs(graph, node, memo), {}, node.name)
    if node.archetype == Archetype.DETECTOR:
        return []  # a detector emits nothing (rejected at parse time anyway)

    # selector
    dark = [StaticRoute(frozenset({DARK}), {node.name: d}, node.name, ()) for d in sorted(node.dark_positions())]
    if node.shape == SelectorShape.OUTPUT_PORTS:
        others = [StaticRoute(frozenset({DARK}), {node.name: q}, node.name, ()) for q in sorted(node.positions or ()) if q != port]
        if port is None or port not in (node.positions or ()):
            return others
        return _extend(_inputs(graph, node, memo), {node.name: port}, node.name) + others
    if node.shape == SelectorShape.FAN_IN:
        routes: list[StaticRoute] = []
        for pos, upstream in node.fan_in.items():
            routes += _extend(_routes_out(graph, graph.nodes[upstream], None, memo), {node.name: pos}, node.name)
        return routes + dark
    # GATE
    aspects_off = {state_key(node.name, a.name): a.off for a in node.aspects}
    routes = []
    for p in sorted(node.transmitting_positions()):
        routes += _extend(_inputs(graph, node, memo), {node.name: p, **aspects_off}, node.name)
    for d in sorted(node.dark_positions()):
        routes.append(StaticRoute(frozenset({DARK}), {node.name: d, **aspects_off}, node.name, ()))
        for a in node.aspects:
            routes.append(
                StaticRoute(frozenset({a.emits}), {node.name: d, **aspects_off, state_key(node.name, a.name): a.on}, node.name, ())
            )
    return routes
