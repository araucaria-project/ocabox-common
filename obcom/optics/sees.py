"""``sees(detector)`` — the one primitive. Walk upstream from the detector reading each
component's transfer table at its proven state and return the set of everything that arrives:
source classes, ``dark`` with the blocking component as terminal, ``undefined`` with the unknown
component as terminal. Never empty: the worst steady state is ``dark``, the worst unknown is
``undefined``. The walk knows no mirrors, covers or domes — only tables.
"""

from __future__ import annotations

from functools import partial
from typing import Callable

from datamodels.optics import DARK, UNDEFINED, Archetype, SeesRecord

from obcom.optics.graph import Node, OpticalGraph
from obcom.optics.kinds import IN, OUT, AxisValues, Emit, Signals, Transmit, is_dark
from obcom.optics.state import ProvenState, axis_values

#: the transfer-table row of a component now: :func:`axis_values` over a proven state, or the
#: positions a service published (:func:`obcom.optics.ribbon.published_values`)
ValuesOf = Callable[[Node], AxisValues]


def sees(graph: OpticalGraph, state: ProvenState, detector: str) -> frozenset[SeesRecord]:
    """What ``detector`` sees now. Records carry provenance: ``terminal`` and the components
    crossed (``via``) between it and the detector."""
    node = graph.node(detector)
    if node.archetype != Archetype.DETECTOR:
        raise ValueError(f"{detector!r} is a {node.archetype}, not a detector")
    return _arriving(graph, partial(axis_values, graph, state), node, IN, {})


def emerging(graph: OpticalGraph, values_of: ValuesOf, name: str, ports: frozenset[str] = frozenset({OUT}), memo=None) -> frozenset[SeesRecord]:
    """What leaves component ``name`` towards a downstream hanging on its ``ports`` — the same walk
    as :func:`sees`, from any component and over any row source (``memo`` shares the walk between
    calls on one state)."""
    return _from(graph, values_of, graph.node(name), ports, {} if memo is None else memo)


def available_classes(graph: OpticalGraph, state: ProvenState) -> frozenset[str]:
    """Every light class some component of the telescope puts out *now* — what ``when:``
    conditions and ``impossible`` are judged against. ``dark`` and ``undefined`` are not light."""
    classes: set[str] = set()
    for node in graph.nodes.values():
        for signals in node.table(axis_values(graph, state, node)).values():
            classes |= {s.light for s in signals if isinstance(s, Emit)}
    return frozenset(classes - {DARK, UNDEFINED})


def source_emission(graph: OpticalGraph, state: ProvenState, node: Node) -> str:
    """The class a source puts out now (``undefined`` when its state cannot be decided)."""
    (signal,) = node.table(axis_values(graph, state, node))[OUT]
    assert isinstance(signal, Emit)
    return signal.light


def outgoing(node: Node, values: AxisValues, ports: frozenset[str]) -> Signals:
    """What leaves ``node`` towards a downstream hanging on ``ports`` (one edge, live on any of
    them): the union of the ports' signals, where ``dark`` from an unselected port is not what
    arrives while another port carries light."""
    table = node.table(values)
    signals: set = set()
    for port in ports:
        signals |= table.get(port if node.outputs is not None else OUT, frozenset())
    if not signals:
        return frozenset({Emit(DARK)})
    if any(not is_dark(s) for s in signals):
        signals = {s for s in signals if not is_dark(s)}
    return frozenset(signals)


# --- internals ---------------------------------------------------------------------------------


def _through(records: frozenset[SeesRecord], node: str) -> frozenset[SeesRecord]:
    return frozenset(SeesRecord(light_class=r.light_class, terminal=r.terminal, via=r.via + (node,)) for r in records)


def _arriving(graph: OpticalGraph, values_of: ValuesOf, node: Node, in_port: str, memo) -> frozenset[SeesRecord]:
    result: set[SeesRecord] = set()
    for feed in node.feeds.get(in_port, ()):
        result |= _from(graph, values_of, graph.nodes[feed.upstream], feed.ports, memo)
    return frozenset(result)


def _from(graph: OpticalGraph, values_of: ValuesOf, node: Node, ports: frozenset[str], memo) -> frozenset[SeesRecord]:
    key = (node.name, ports)
    if key in memo:
        return memo[key]
    result: set[SeesRecord] = set()
    for signal in outgoing(node, values_of(node), ports):
        if isinstance(signal, Emit):
            result.add(SeesRecord(light_class=signal.light, terminal=node.name, via=()))
        else:
            assert isinstance(signal, Transmit)
            result |= _through(_arriving(graph, values_of, node, signal.port, memo), node.name)
    memo[key] = frozenset(result)
    return memo[key]
