"""Optical Path Model v4 — the reference solver (ALMA doctrine: one solver, many callers).

Pure functions over ``(graph, proven state)``, no I/O::

    graph = parse_graph(components)                     # or GraphInvalid with every reason
    sees(graph, state, "camera")                        # frozenset[SeesRecord]
    check(graph, state, "camera", "object")             # Active | Settable | Collision | Impossible
    resolve(graph, state, "camera", "dark")             # {selector: symbol} | None
    ribbon(graph, state, "camera")                      # the path to the detector, emitter first
    schematic(graph)                                    # places and wires on a grid, for every renderer
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
from obcom.optics.layout import Place, Schematic, Wire, schematic
from obcom.optics.ribbon import Ribbon, RibbonStop, StopRole, published_values, ribbon, seen_positions
from obcom.optics.sees import ValuesOf, available_classes, emerging, outgoing, sees
from obcom.optics.labels import component_label, label
from obcom.optics.state import Declaration, Hold, ProvenState, Resolved, proven_position, published_state, resolve_axes
from obcom.optics.telemetry import Readings, proven_state, tertiary_symbol

__all__ = [
    "parse_graph", "validate_graph", "GraphInvalid", "OpticalGraph", "Node", "Feed",
    "sees", "available_classes", "outgoing", "emerging", "ValuesOf",
    "schematic", "Schematic", "Place", "Wire",
    "ribbon", "Ribbon", "RibbonStop", "StopRole", "published_values", "seen_positions",
    "check", "check_result", "resolve", "UnknownFunction",
    "compile_telescope", "compile_observatory", "authored_hash", "CompileError",
    "enumerate_routes", "routes_for_goal", "StaticRoute",
    "ProvenState", "Hold", "Declaration", "Resolved", "proven_position", "published_state", "resolve_axes",
    "label", "component_label",
    "Readings", "proven_state", "tertiary_symbol",
    "KindRegistry", "DEFAULT_REGISTRY", "default_registry", "Kind", "Source", "ConstantSource", "SkySource",
    "Selector", "SelectorShape", "Aspect", "Axis", "DomeKind", "Splitter", "Passive", "Detector",
    "Transmit", "Emit", "IN", "OUT",
]
