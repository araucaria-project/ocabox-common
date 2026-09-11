"""Optical Path Model v4 — the reference solver (ALMA doctrine: one solver, many callers).

Pure functions over ``(graph, proven state)``, no I/O::

    graph = parse_graph(components)                     # or GraphInvalid with every reason
    sees(graph, state, "camera")                        # frozenset[SeesRecord]
    check(graph, state, "camera", "object")             # Active | Settable | Collision | Impossible
    resolve(graph, state, "camera", "dark")             # {selector: symbol} | None
    compile_telescope(graph) / compile_observatory({...})   # route table + conflict map, verified

Vocabularies and result shapes come from ``datamodels.optics``; the kind → archetype registry is
:data:`DEFAULT_REGISTRY` (injectable). Spec: knowledge-base ``Architecture/Optical Path Model.md``;
epic araucaria-project/ocabox-server#27; this package: araucaria-project/ocabox-common#23.
"""

from obcom.optics.check import UnknownFunction, check, check_result, resolve
from obcom.optics.compile import CompileError, authored_hash, compile_observatory, compile_telescope
from obcom.optics.graph import Feed, GraphInvalid, Node, OpticalGraph, parse_graph, validate_graph
from obcom.optics.kinds import (
    DEFAULT_REGISTRY,
    IN,
    OUT,
    Aspect,
    Axis,
    ConstantSource,
    Detector,
    DomeKind,
    Emit,
    Kind,
    KindRegistry,
    Passive,
    Selector,
    SelectorShape,
    SkySource,
    Source,
    Splitter,
    Transmit,
    default_registry,
)
from obcom.optics.routes import StaticRoute, enumerate_routes, routes_for_goal
from obcom.optics.sees import available_classes, outgoing, sees
from obcom.optics.state import Hold, ProvenState, Resolved, proven_position, resolve_axes

__all__ = [
    "parse_graph", "validate_graph", "GraphInvalid", "OpticalGraph", "Node", "Feed",
    "sees", "available_classes", "outgoing",
    "check", "check_result", "resolve", "UnknownFunction",
    "compile_telescope", "compile_observatory", "authored_hash", "CompileError",
    "enumerate_routes", "routes_for_goal", "StaticRoute",
    "ProvenState", "Hold", "Resolved", "proven_position", "resolve_axes",
    "KindRegistry", "DEFAULT_REGISTRY", "default_registry", "Kind", "Source", "ConstantSource", "SkySource",
    "Selector", "SelectorShape", "Aspect", "Axis", "DomeKind", "Splitter", "Passive", "Detector",
    "Transmit", "Emit", "IN", "OUT",
]
