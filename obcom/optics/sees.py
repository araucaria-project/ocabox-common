"""``sees(detector)`` — the one primitive. Walk upstream from the detector following *proven*
selector states and return the set of everything that reaches it: source classes, ``dark`` with
the blocking component as terminal, ``undefined`` with the unknown selector as terminal. Never
empty: the worst steady state is ``dark``, the worst unknown is ``undefined``.
"""

from __future__ import annotations

from datamodels.optics import DARK, UNDEFINED, Archetype, SeesRecord

from obcom.optics.graph import Node, OpticalGraph
from obcom.optics.kinds import SelectorShape, Source
from obcom.optics.state import ProvenState, Unknown, proven_position


def sees(graph: OpticalGraph, state: ProvenState, detector: str) -> frozenset[SeesRecord]:
    """What ``detector`` sees now. Records carry provenance: ``terminal`` and the components
    crossed (``via``) between it and the detector."""
    node = graph.node(detector)
    if node.archetype != Archetype.DETECTOR:
        raise ValueError(f"{detector!r} is a {node.archetype}, not a detector")
    memo: dict[tuple[str, str | None], frozenset[SeesRecord]] = {}
    return _inputs(graph, state, node, memo)


def available_classes(graph: OpticalGraph, state: ProvenState) -> frozenset[str]:
    """Every light class some source (or emitting aspect) of the telescope puts out *now* — what
    ``when:`` conditions and ``impossible`` are judged against. ``dark`` and ``undefined`` are
    not light and are left out."""
    classes: set[str] = set()
    for node in graph.nodes.values():
        if isinstance(node.kind, Source):
            classes.add(source_emission(graph, state, node))
        for aspect in node.aspects:
            if proven_position(graph, state, node, aspect.name) == aspect.on:
                classes.add(aspect.emits)
    return frozenset(classes - {DARK, UNDEFINED})


def _record(light_class: str, terminal: str) -> SeesRecord:
    return SeesRecord(light_class=light_class, terminal=terminal)


def _through(records: frozenset[SeesRecord], node: str) -> frozenset[SeesRecord]:
    return frozenset(SeesRecord(light_class=r.light_class, terminal=r.terminal, via=r.via + (node,)) for r in records)


def source_emission(graph: OpticalGraph, state: ProvenState, node: Node) -> str:
    """The class a source puts out now, judged like a selector: telemetry that is moving, stale,
    unmapped or ``None`` makes the source ``undefined``; no telemetry at all asserts nothing and
    the kind decides (a lamp is taken as lit, the sky needs the sun)."""
    assert isinstance(node.kind, Source)
    position = proven_position(graph, state, node)
    if position is Unknown.UNDEFINED:
        return UNDEFINED
    pos = None if position is Unknown.ABSENT else position
    emitted = node.kind.emission(node.spec, pos, state.environment)
    return UNDEFINED if emitted is None else emitted


def _inputs(graph: OpticalGraph, state: ProvenState, node: Node, memo) -> frozenset[SeesRecord]:
    result: set[SeesRecord] = set()
    for edge in node.inputs:
        if edge.downstream_position is not None:
            continue  # fan-in: the selector picks one input itself
        result |= _out(graph, state, graph.nodes[edge.upstream], edge.upstream_port, memo)
    return frozenset(result)


def _out(graph: OpticalGraph, state: ProvenState, node: Node, port: str | None, memo) -> frozenset[SeesRecord]:
    key = (node.name, port)
    if key not in memo:
        memo[key] = _compute_out(graph, state, node, port, memo)
    return memo[key]


def _compute_out(graph: OpticalGraph, state: ProvenState, node: Node, port: str | None, memo) -> frozenset[SeesRecord]:
    name = node.name
    if node.archetype == Archetype.SOURCE:
        return frozenset({_record(source_emission(graph, state, node), name)})
    if node.archetype in (Archetype.PASSIVE, Archetype.SPLITTER):
        return _through(_inputs(graph, state, node, memo), name)
    if node.archetype == Archetype.DETECTOR:
        return frozenset()

    position = proven_position(graph, state, node)
    if isinstance(position, Unknown):
        return frozenset({_record(UNDEFINED, name)})

    if node.shape == SelectorShape.OUTPUT_PORTS:
        if position == port:
            return _through(_inputs(graph, state, node, memo), name)
        return frozenset({_record(DARK, name)})

    if node.shape == SelectorShape.FAN_IN:
        upstream = node.fan_in.get(position)
        if upstream is None:
            return frozenset({_record(DARK, name)})  # a non-input position (closed dome, parked mirror)
        return _through(_out(graph, state, graph.nodes[upstream], None, memo), name)

    # GATE: the cover axis, then the emitting aspects on top
    result: set[SeesRecord] = set()
    emitting = False
    for aspect in node.aspects:
        a = proven_position(graph, state, node, aspect.name)
        if a is Unknown.UNDEFINED:
            result.add(_record(UNDEFINED, name))
        elif a == aspect.on:
            result.add(_record(aspect.emits, name))
            emitting = True
    if position in node.transmitting_positions():
        result |= _through(_inputs(graph, state, node, memo), name)
    elif not emitting:
        result.add(_record(DARK, name))  # a closed cover lit by its own lamp is `lamp`, not `dark`
    return frozenset(result)
