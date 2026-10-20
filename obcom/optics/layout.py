"""The schematic of a telescope's optics: where each component sits, how the light wires run
between them, and where each label goes — one pure layout for every renderer (TOI's QPainter,
owies' SVG, matplotlib reports), so the schematic looks the same everywhere and nothing is drawn by
hand.

Grid: a *column* is a layer along the light (sources first, detectors last), a *row* a lane across
it; both may be fractional. ``display: {x: column, y: row}`` in the config places a component by
hand; the rest is laid out automatically, as a tidy tree from the main source:

- the light goes straight on; where a switch sends it to branches of the same depth (M3 to the
  camera and to BESO) they open symmetrically, the switch centred between them, a corridor wide
  enough for both branches' labels;
- a short branch (a guider off its pick-off) stands beside its switch, on the outer side;
- what feeds the picture from aside stands beside what it feeds (the lamp mirror beside M4); inputs
  meeting in one component sit side by side above it, centred (the sky and the flat screen over the
  dome, the lamps over their mirror).

Wires are orthogonal polylines through component centres (a renderer rounds the corners): out of a
switch across at its level, then down; into a fan-in down, across halfway, down into it.

:func:`place_labels` puts every label beside, under or over its component where it hits no wire,
component or other label, given the sizes a renderer measured.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from datamodels.optics import Archetype

from obcom.optics.graph import Feed, Node, OpticalGraph
from obcom.optics.kinds import OUT, Transmit
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


@dataclass(frozen=True)
class Label:
    """Where the label of one component goes, in the drawing's own units."""

    name: str
    side: str  #: ``right`` | ``left`` | ``below`` | ``above`` of its component
    x: float
    y: float
    width: float
    height: float
    clear: bool  #: it hits no wire, component or other label

    @property
    def box(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.width, self.height


SIDES = ("right", "left", "below", "above")  #: in order of preference


def place_labels(layout: Schematic, sizes: Mapping[str, tuple[float, float]], center: Callable[[Point], tuple[float, float]],
                 radius: float, bounds: tuple[float, float, float, float], gap: float = 4.0,
                 sides: tuple[str, ...] = SIDES, narrow: float | None = None, rounds: int = 3) -> dict[str, Label]:
    """Each label of ``sizes`` (component → width, height as the renderer measured them) beside,
    under or over its component, where it hits nothing: no wire, no component, no other label,
    nothing outside ``bounds`` (x, y, width, height). ``center`` maps a grid point to the drawing
    (the renderer's own scale and orientation), ``radius`` is a component's; a label under or over
    its component is at most ``narrow`` wide (the renderer cuts it to its box).

    Each label takes the side that hits least — by how much it covers, not how often — first the
    most constrained, then a few ``rounds`` of each one reconsidering among the others; ties go to
    the first of ``sides``. ``clear`` is false where nothing better than a collision was left."""
    nodes = {name: center((p.column, p.row)) for name, p in layout.places.items()}
    solids = [(x - radius, y - radius, 2 * radius, 2 * radius) for x, y in nodes.values()]
    wires = []
    for wire in layout.wires:
        points = [center(p) for p in wire.points]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            wires.append((min(x0, x1) - 1, min(y0, y1) - 1, abs(x1 - x0) + 2, abs(y1 - y0) + 2))

    def candidates(name: str) -> list[tuple[str, tuple[float, float, float, float]]]:
        (x, y), (w, h) = nodes[name], sizes[name]
        across = w if narrow is None else min(w, narrow)
        boxes = {"right": (x + radius + gap, y - h / 2, w, h), "left": (x - radius - gap - w, y - h / 2, w, h),
                 "below": (x - across / 2, y + radius + gap / 2, across, h),
                 "above": (x - across / 2, y - radius - gap / 2 - h, across, h)}
        return [(side, boxes[side]) for side in sides]

    def cost(box, others) -> float:
        area = box[2] * box[3] or 1.0
        covered = sum(_area(box, o) for o in solids) + sum(_area(box, o) for o in others)
        return (10 * covered / area + 2 * sum(_area(box, w) > 0 for w in wires)
                + 10 * (area - _area(box, bounds)) / area)

    order = sorted((name for name in sizes if name in nodes),
                   key=lambda name: sum(cost(box, []) == 0 for _, box in candidates(name)))
    chosen: dict[str, int] = {}  #: name → index of its candidate

    def score(name: str, i: int) -> float:
        others = [candidates(other)[j][1] for other, j in chosen.items() if other != name]
        return cost(candidates(name)[i][1], others) + 0.01 * i

    def best(name: str) -> int:
        return min(range(len(sides)), key=lambda i: score(name, i))

    for name in order:
        chosen[name] = best(name)
    for _ in range(rounds):
        moved = False
        for name in order:
            again = best(name)
            if score(name, again) < score(name, chosen[name]) - 1e-9:
                chosen[name], moved = again, True
        if not moved:
            break
    labels = {}
    for name, i in chosen.items():
        side, box = candidates(name)[i]
        labels[name] = Label(name, side, *box, clear=score(name, i) < 0.5)
    return labels


def _area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """The area ``a`` and ``b`` have in common."""
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(w, 0.0) * max(h, 0.0)


# --- internals ---------------------------------------------------------------------------------


def _downstreams(graph: OpticalGraph) -> dict[str, list[str]]:
    """Component → those it feeds, in authored order."""
    down: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes.values():
        for upstream in dict.fromkeys(f.upstream for feeds in node.feeds.values() for f in feeds):
            down[upstream].append(node.name)
    return down


#: lanes between neighbours: branches of a symmetric split leave a corridor for both their labels
#: (detectors side by side only one label's room), a short branch or a side feeder stands one
#: label away, inputs meeting in one component closer
MAJOR_GAP, MINOR_GAP, INPUT_GAP = 4.0, 1.5, 1.2

_Shape = list[tuple[str, float, float]]  #: (name, column, row) — a subtree, rows relative to its root


class _Tidy:
    """The automatic layout: tidy trees from the sources, see the module docstring."""

    def __init__(self, graph: OpticalGraph):
        self.graph = graph
        self.down = _downstreams(graph)
        self.claimed: set[str] = set()
        self.parent: dict[str, str] = {}
        self.depth: dict[str, int] = {}

    def places(self) -> dict[str, tuple[float, float]]:
        shape: _Shape = []
        for root in _sources_first(self.graph):
            if root in self.claimed:
                continue
            self._tree(root)
            start, column, above = root, 0.0, False
            only = [d for d in self.down.get(root, ()) if self.parent.get(d) == root]
            if len(only) == 1 and len(self.down.get(root, ())) == 1 and len(_ordered_upstreams(self.graph.nodes[only[0]])) > 1:
                start, column, above = only[0], 1.0, True  # the sky meets the flat screen at the dome: both over it
            tree = self._down(start, column, 0, above)
            shape += _shifted(tree, _offset(shape, tree, +1, MAJOR_GAP) if shape else 0.0)
        left = min(r for _, _, r in shape)
        top = min(c for _, c, _ in shape)
        return {name: (column - top, row - left) for name, column, row in shape}

    def _tree(self, root: str) -> None:
        """Tree parents and depths (the longest way to a leaf) of what ``root`` reaches unclaimed."""
        stack = [root]
        seen = {root}
        order = []
        while stack:
            name = stack.pop()
            order.append(name)
            for d in reversed(self.down.get(name, ())):
                if d not in seen and d not in self.claimed:
                    seen.add(d)
                    self.parent[d] = name
                    stack.append(d)
        for name in reversed(order):
            children = [d for d in self.down.get(name, ()) if self.parent.get(d) == name]
            self.depth[name] = max((self.depth[c] + 1 for c in children), default=0)

    def _down(self, name: str, column: float, side: int, above: bool) -> _Shape:
        """``name`` at row 0 and what it feeds; ``side`` is where its branch lies (-1 left, +1 right,
        0 the middle): short branches and side feeders go further out."""
        self.claimed.add(name)
        shape: _Shape = [(name, column, 0.0)]
        outward = side or 1
        children = [c for c in self.down.get(name, ()) if self.parent.get(c) == name and c not in self.claimed]
        deepest = max((self.depth[c] for c in children), default=0)
        majors = [c for c in children if self.depth[c] == deepest]
        if len(majors) >= 2:  # a switch into equal branches: they open symmetrically around it
            sides = [-1] + [0] * (len(majors) - 2) + [1]
            shapes = [self._down(c, column + 1, s, False) for c, s in zip(majors, sides)]
            offsets = _row_of(shapes, MAJOR_GAP if deepest else MINOR_GAP)  # leaves keep their labels under them
            middle = (offsets[0] + offsets[-1]) / 2
            for sub, offset in zip(shapes, offsets):
                shape += _shifted(sub, offset - middle)
            minors = [c for c in children if c not in majors]
        else:  # the deepest goes straight on
            if children:
                shape += self._down(majors[0], column + 1, side, False)
            minors = [c for c in children if c not in majors[:1]]
        for c in minors:  # a short branch beside its switch, further out
            sub = self._down(c, column, outward, False)
            shape += _shifted(sub, _offset(shape, sub, outward, MINOR_GAP))
        inputs = [u for u in _ordered_upstreams(self.graph.nodes[name]) if u not in self.claimed]
        if above:
            shape += self._over(inputs, column - 1)
        for u in inputs:  # what feeds it from aside, beside it, further out
            if u not in self.claimed:
                sub = self._feeder(u, column, outward)
                shape += _shifted(sub, _offset(shape, sub, outward, MINOR_GAP))
        return shape

    def _feeder(self, name: str, column: float, side: int) -> _Shape:
        """A component feeding the picture from aside, its own inputs side by side over it."""
        self.claimed.add(name)
        return self._with_inputs(name, column)

    def _with_inputs(self, name: str, column: float) -> _Shape:
        inputs = [u for u in _ordered_upstreams(self.graph.nodes[name]) if u not in self.claimed]
        return [(name, column, 0.0)] + self._over(inputs, column - 1)

    def _over(self, inputs: list[str], column: float) -> _Shape:
        """``inputs`` side by side in ``column``, centred over row 0 (claimed together first, so
        none of them is taken as another's input)."""
        self.claimed.update(inputs)
        shapes = [self._with_inputs(u, column) for u in inputs]
        if not shapes:
            return []
        offsets = _row_of(shapes, INPUT_GAP)
        middle = (offsets[0] + offsets[-1]) / 2
        return [p for sub, offset in zip(shapes, offsets) for p in _shifted(sub, offset - middle)]


def _shifted(shape: _Shape, by: float) -> _Shape:
    return [(name, column, row + by) for name, column, row in shape]


def _extent(shape: _Shape) -> dict[float, tuple[float, float]]:
    """Column → (lowest, highest) row taken."""
    out: dict[float, tuple[float, float]] = {}
    for _, column, row in shape:
        lo, hi = out.get(column, (row, row))
        out[column] = (min(lo, row), max(hi, row))
    return out


def _offset(placed: _Shape, new: _Shape, direction: int, gap: float) -> float:
    """How far to shift ``new`` (towards ``direction``) to keep ``gap`` rows from ``placed`` in every
    column both take."""
    have, want = _extent(placed), _extent(new)
    common = set(have) & set(want)
    if direction > 0:
        if not common:
            return max(hi for _, hi in have.values()) + gap - min(lo for lo, _ in want.values())
        return max(have[c][1] + gap - want[c][0] for c in common)
    if not common:
        return min(lo for lo, _ in have.values()) - gap - max(hi for _, hi in want.values())
    return min(have[c][0] - gap - want[c][1] for c in common)


def _row_of(shapes: list[_Shape], gap: float) -> list[float]:
    """Offsets laying ``shapes`` left to right, ``gap`` apart."""
    offsets, placed = [0.0], list(shapes[0])
    for sub in shapes[1:]:
        offsets.append(_offset(placed, sub, +1, gap))
        placed += _shifted(sub, offsets[-1])
    return offsets


def _auto_places(graph: OpticalGraph) -> dict[str, tuple[float, float]]:
    return _Tidy(graph).places()


def _sources_first(graph: OpticalGraph) -> Iterable[str]:
    """Sources in authored order, then any component nothing feeds (a mis-hung part)."""
    yield from graph.sources
    yield from (n for n, node in graph.nodes.items() if not node.upstreams and n not in graph.sources)


def _ordered_upstreams(node: Node) -> list[str]:
    return list(dict.fromkeys(f.upstream for feeds in node.feeds.values() for f in feeds))


def _route(up: Place, down: Place, node: Node) -> tuple[Point, ...]:
    """Out of a switch across at its level, then down; into a fan-in from above down, across
    halfway, down into it; beside one another straight across."""
    start, end = (up.column, up.row), (down.column, down.row)
    if up.row == down.row or up.column == down.column:
        return start, end
    if down.column > up.column and len(_ordered_upstreams(node)) > 1:
        middle = down.column - 0.5
        return start, (middle, up.row), (middle, down.row), end
    return start, (up.column, down.row), end
