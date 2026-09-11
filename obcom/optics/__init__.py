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
from obcom.optics.graph import Edge, GraphInvalid, Node, OpticalGraph, parse_graph, validate_graph
from obcom.optics.kinds import (
    DEFAULT_REGISTRY,
    Aspect,
    ConstantSource,
    Detector,
    DomeKind,
    Kind,
    KindRegistry,
    Passive,
    Selector,
    SelectorShape,
    SkySource,
    Source,
    Splitter,
    default_registry,
)
from obcom.optics.routes import StaticRoute, enumerate_routes, routes_for_goal
from obcom.optics.sees import available_classes, sees
from obcom.optics.state import Hold, ProvenState, Unknown, proven_position

__all__ = [
    "parse_graph", "validate_graph", "GraphInvalid", "OpticalGraph", "Node", "Edge",
    "sees", "available_classes",
    "check", "check_result", "resolve", "UnknownFunction",
    "compile_telescope", "compile_observatory", "authored_hash", "CompileError",
    "enumerate_routes", "routes_for_goal", "StaticRoute",
    "ProvenState", "Hold", "Unknown", "proven_position",
    "KindRegistry", "DEFAULT_REGISTRY", "default_registry", "Kind", "Source", "ConstantSource", "SkySource",
    "Selector", "SelectorShape", "Aspect", "DomeKind", "Splitter", "Passive", "Detector",
]
