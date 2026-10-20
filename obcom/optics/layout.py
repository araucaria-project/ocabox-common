"""The schematic of a telescope's optics: where each component sits and how the light wires run
between them, in grid cells — one pure layout for every renderer (TOI's QPainter, owies' SVG,
matplotlib reports), so the schematic looks the same everywhere and nothing is drawn by hand.

Placement: ``display: {x: column, y: row}`` in the config puts a component on the grid (columns
follow the light, sources left, detectors right; rows are branches). Unplaced components are laid
out automatically, so a new telescope draws without any hint:

- column: the longest way from the first source (layered left to right);
- row: the light goes straight on — the downstream with the longest way to a detector keeps the
  row, every further branch opens the next free row;
- what only feeds the picture from aside (the lamps and their mirror into the BESO entrance
  mirror, the flat screen into the dome) sits one column left of what it feeds, its first input
  straight on, the others in the nearest free cell below.

Wires are orthogonal polylines through cell centres; a renderer rounds the corners. A branch leaves
its switch vertically (out of M3, out of a pick-off); an input enters a fan-in vertically (into M4,
into the dome).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from datamodels.optics import Archetype

from obcom.optics.graph import Feed, Node, OpticalGraph
from obcom.optics.kinds import IN, OUT, Transmit
from obcom.optics.sees import ValuesOf, emerging

Point = tuple[float, float]


@dataclass(frozen=True)
class Place:
    name: str
    column: float
    row: float
    authored: bool  #: placed by ``display.x``/``display.y`` rather than automatically


@dataclass(frozen=True)
class Wire:
    upstream: str
    downstream: str
    feed: Feed  #: the edge it draws: which output ports of ``upstream`` it hangs on
    input: str  #: the downstream's input port (a fan-in position, e.g. ``calib``; ``in`` otherwise)
    points: tuple[Point, ...]  #: from the upstream's centre to the downstream's, in grid cells

    def taken(self, graph: OpticalGraph, values_of: ValuesOf) -> bool:
        """The downstream passes what this wire brings (a detector always takes it): a wire not
        taken is a possible way, drawn idle whatever travels on it (the flat screen while the dome
        looks at the sky)."""
        node = graph.node(self.downstream)
        if node.archetype == Archetype.DETECTOR:
            return True
        return any(isinstance(s, Transmit) and s.port == self.input for signals in node.table(values_of(node)).values() for s in signals)

    @property
    def port(self) -> str | None:
        """The upstream's output port it leaves by (``andor`` out of M3), ``None`` for a plain output."""
        return None if self.feed.ports == frozenset({OUT}) else ", ".join(sorted(self.feed.ports))


@dataclass(frozen=True)
class Schematic:
    places: Mapping[str, Place]
    wires: tuple[Wire, ...]

    @property
    def columns(self) -> float:
        return max((p.column for p in self.places.values()), default=-1) + 1

    @property
    def rows(self) -> float:
        return max((p.row for p in self.places.values()), default=-1) + 1

    def light(self, graph: OpticalGraph, values_of: ValuesOf) -> dict[Wire, frozenset[str]]:
        """What travels on every wire now (light classes, ``dark``, ``undefined``)."""
        memo: dict = {}
        return {w: frozenset(r.light_class for r in emerging(graph, values_of, w.upstream, w.feed.ports, memo)) for w in self.wires}


def schematic(graph: OpticalGraph) -> Schematic:
    """The layout of ``graph``: authored places where given, automatic elsewhere."""
    auto = _auto_places(graph)
    places = {}
    for name in graph.nodes:
        display = graph.components[name].display
        if display is not None and display.x is not None and display.y is not None:
            places[name] = Place(name, float(display.x), float(display.y), True)
        else:
            places[name] = Place(name, *auto[name], False)
    wires = tuple(
        Wire(feed.upstream, node.name, feed, port, _route(places[feed.upstream], places[node.name], node))
        for node in graph.nodes.values() for port, feeds in node.feeds.items() for feed in feeds
    )
    return Schematic(places, wires)


# --- internals ---------------------------------------------------------------------------------


def _downstreams(graph: OpticalGraph) -> dict[str, list[str]]:
    """Component → those it feeds, in authored order."""
    down: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes.values():
        for upstream in dict.fromkeys(f.upstream for feeds in node.feeds.values() for f in feeds):
            down[upstream].append(node.name)
    return down


def _columns(graph: OpticalGraph, down: Mapping[str, list[str]]) -> dict[str, int]:
    column: dict[str, int] = {}

    def layer(name: str) -> int:
        if name not in column:
            column[name] = max((layer(u) + 1 for u in graph.nodes[name].upstreams), default=0)
        return column[name]

    for name in graph.nodes:
        layer(name)
    return column


def _depths(graph: OpticalGraph, down: Mapping[str, list[str]]) -> dict[str, int]:
    """Component → the longest way from it to a detector: the main line is the deepest."""
    depth: dict[str, int] = {}

    def walk(name: str) -> int:
        if name not in depth:
            depth[name] = max((walk(d) + 1 for d in down.get(name, ())), default=0)
        return depth[name]

    for name in graph.nodes:
        walk(name)
    return depth


def _auto_places(graph: OpticalGraph) -> dict[str, tuple[float, float]]:
    down = _downstreams(graph)
    depth = _depths(graph, down)
    column = _columns(graph, down)
    row: dict[str, int] = {}
    taken: set[tuple[int, int]] = set()
    free = 0

    def put(name: str, r: int) -> None:
        nonlocal free
        row[name] = r
        taken.add((column[name], r))
        free = max(free, r + 1)

    def downstream(name: str, r: int) -> None:
        """Place ``name`` on row ``r`` and its downstreams: the main line straight on, branches below."""
        put(name, r)
        branches = sorted((n for n in down.get(name, ()) if n not in row), key=lambda n: -depth[n])
        for i, d in enumerate(branches):
            downstream(d, r if i == 0 else free)

    def upstream(name: str) -> None:
        """Components feeding a placed ``name`` that nothing placed yet: beside it, the first input
        straight on, the others in the nearest free cells below."""
        for u in _ordered_upstreams(graph.nodes[name]):
            if u in row:
                continue
            column[u] = column[name] - 1
            r = row[name]
            while (column[u], r) in taken:
                r += 1
            put(u, r)
            upstream(u)
            for d in down.get(u, ()):
                if d not in row:
                    downstream(d, free)

    for source in _sources_first(graph):
        if source not in row:  # a source no earlier tree reached: a picture of its own
            downstream(source, free)
            for name in list(row):
                upstream(name)
    return {name: (float(column[name]), float(row[name])) for name in graph.nodes}


def _sources_first(graph: OpticalGraph) -> Iterable[str]:
    """Sources in authored order, then any component nothing feeds (a mis-hung part)."""
    yield from graph.sources
    yield from (n for n, node in graph.nodes.items() if not node.upstreams and n not in graph.sources)


def _ordered_upstreams(node: Node) -> list[str]:
    return list(dict.fromkeys(f.upstream for feeds in node.feeds.values() for f in feeds))


def _route(up: Place, down: Place, node: Node) -> tuple[Point, ...]:
    start, end = (up.column, up.row), (down.column, down.row)
    if up.row == down.row or up.column == down.column:
        return start, end
    fan_in = len(node.feeds) > 1 or (node.archetype == Archetype.SELECTOR and IN not in node.feeds)
    corner = (down.column, up.row) if fan_in else (up.column, down.row)
    return start, corner, end
