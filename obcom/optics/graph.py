"""The optical graph: ``parse_graph`` turns a telescope's ``components:`` into validated nodes and
edges, or refuses with every reason at once (``GraphInvalid`` → the ``Invalid`` verdict).

Everything a config typo can express is caught here, at load time: unknown kinds and components,
references to undeclared ports, two components hanging on one port without a splitter, ports on
components that have none, cycles, ``paths`` goals no source can ever satisfy, ``via`` outside
the detector's upstream cone. Runtime state never enters this module.

Reporting: structural errors and cycles are collected together and raised at once; ``paths``
validation needs a structurally sound, acyclic graph (it enumerates routes) and therefore runs
only when the first pass found nothing — its errors are then reported all at once as well.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from datamodels.optics import (
    DARK,
    Archetype,
    ConfigError,
    DetectorPaths,
    Invalid,
    OpticalComponentSpec,
    PortOwner,
    TelescopeOpticsSpec,
    split_state_key,
)
from pydantic import ValidationError

from obcom.optics.kinds import DEFAULT_REGISTRY, Aspect, Kind, KindRegistry, Selector, SelectorShape, Source


class GraphInvalid(ValueError):
    """The authored graph is not usable. Carries every ``ConfigError`` found, not just the first."""

    def __init__(self, errors: Iterable[ConfigError]):
        self.errors: list[ConfigError] = list(errors)
        super().__init__("; ".join(f"[{e.code}] {e.message}" for e in self.errors))

    @property
    def verdict(self) -> Invalid:
        return Invalid(errors=self.errors)


@dataclass(frozen=True)
class Edge:
    """Light flows ``upstream`` → ``downstream``. ``port`` names the port of the *owner*: for an
    UPSTREAM-owned edge it is an output port of ``upstream`` (``from: {tertiary: andor}``); for a
    SELF-owned edge it is an input position of ``downstream`` (``inputs: {open: sky}``); ``None``
    is a passive edge from the upstream's single output."""

    upstream: str
    downstream: str
    port: str | None = None
    owner: PortOwner | None = None

    @property
    def upstream_port(self) -> str | None:
        return self.port if self.owner == PortOwner.UPSTREAM else None

    @property
    def downstream_position(self) -> str | None:
        return self.port if self.owner == PortOwner.SELF else None


@dataclass(frozen=True)
class Node:
    name: str
    kind: Kind
    spec: OpticalComponentSpec
    shape: SelectorShape | None = None
    positions: frozenset[str] | None = None  #: declared position vocabulary (selectors; splitters with ``positions:``)
    inputs: tuple[Edge, ...] = ()
    outputs: tuple[Edge, ...] = ()

    @property
    def archetype(self) -> Archetype:
        return self.kind.archetype

    @property
    def aspects(self) -> tuple[Aspect, ...]:
        return self.kind.aspects if isinstance(self.kind, Selector) else ()

    @property
    def fan_in(self) -> dict[str, str]:
        """FAN_IN selectors: position → upstream component."""
        return {e.port: e.upstream for e in self.inputs if e.owner == PortOwner.SELF and e.port is not None}

    def transmitting_positions(self) -> frozenset[str]:
        """Positions in which light passes (GATE: not blocking; FAN_IN: an input; OUTPUT_PORTS: all)."""
        positions = self.positions or frozenset()
        if self.shape == SelectorShape.FAN_IN:
            return frozenset(self.fan_in)
        if self.shape == SelectorShape.GATE:
            assert isinstance(self.kind, Selector)
            return frozenset(p for p in positions if not self.kind.blocks(self.spec, p))
        return positions

    def dark_positions(self) -> frozenset[str]:
        return (self.positions or frozenset()) - self.transmitting_positions()

    @property
    def paths(self) -> DetectorPaths | None:
        return self.spec.paths


@dataclass(frozen=True)
class OpticalGraph:
    nodes: Mapping[str, Node]
    components: Mapping[str, OpticalComponentSpec]  #: every component, optical or not (derived selectors read the mount)
    presets: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    registry: KindRegistry = DEFAULT_REGISTRY
    telescope: str | None = None

    def node(self, name: str) -> Node:
        try:
            return self.nodes[name]
        except KeyError:
            raise KeyError(f"{name!r} is not an optical component of this telescope") from None

    def of_archetype(self, archetype: Archetype) -> tuple[str, ...]:
        return tuple(n.name for n in self.nodes.values() if n.archetype == archetype)

    @property
    def detectors(self) -> tuple[str, ...]:
        return self.of_archetype(Archetype.DETECTOR)

    @property
    def selectors(self) -> tuple[str, ...]:
        return self.of_archetype(Archetype.SELECTOR)

    @property
    def sources(self) -> tuple[str, ...]:
        return self.of_archetype(Archetype.SOURCE)

    def upstream_cone(self, name: str) -> frozenset[str]:
        """Every component light can reach ``name`` from, ``name`` excluded."""
        seen: set[str] = set()
        stack = [e.upstream for e in self.node(name).inputs]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(e.upstream for e in self.nodes[current].inputs)
        return frozenset(seen)

    def possible_classes(self, names: Iterable[str]) -> frozenset[str]:
        """Every light class the given components can ever put on a path."""
        classes: set[str] = set()
        for name in names:
            node = self.nodes[name]
            if isinstance(node.kind, Source):
                classes |= node.kind.possible_classes(node.spec)
            for aspect in node.aspects:
                classes.add(aspect.emits)
        return frozenset(classes)


def validate_graph(components, presets=None, registry: KindRegistry = DEFAULT_REGISTRY) -> Invalid | None:
    """``Invalid`` with every reason, or ``None`` when the graph loads."""
    try:
        parse_graph(components, presets, registry)
    except GraphInvalid as e:
        return e.verdict
    return None


def parse_graph(
    components: Mapping[str, Mapping | OpticalComponentSpec] | TelescopeOpticsSpec,
    presets: Mapping[str, Mapping[str, str]] | None = None,
    registry: KindRegistry = DEFAULT_REGISTRY,
    telescope: str | None = None,
) -> OpticalGraph:
    """Build the optical graph of one telescope from its ``components:`` block (raw config dict or
    already-validated specs). Raises :class:`GraphInvalid` listing every problem found."""
    spec = _validate_shape(components, presets)
    errors: list[ConfigError] = []
    nodes = _build_nodes(spec, registry, errors)
    graph = OpticalGraph(nodes=nodes, components=spec.components, presets=spec.presets, registry=registry, telescope=telescope)
    _check_cycles(graph, errors)  # safe on a partially valid graph: every reported problem, structural and cyclic, at once
    if errors:
        raise GraphInvalid(errors)
    _check_paths(graph, errors)
    if errors:
        raise GraphInvalid(errors)
    return graph


# --- internals ---------------------------------------------------------------------------------


def _validate_shape(components, presets) -> TelescopeOpticsSpec:
    if isinstance(components, TelescopeOpticsSpec):
        return components
    raw_components = {
        name: (c.model_dump(by_alias=True, exclude_none=True) if isinstance(c, OpticalComponentSpec) else c)
        for name, c in components.items()
    }
    try:
        return TelescopeOpticsSpec.model_validate({"components": raw_components, "presets": presets or {}})
    except ValidationError as e:
        raise GraphInvalid(_grammar_errors(e)) from None


def _grammar_errors(e: ValidationError) -> list[ConfigError]:
    errors = []
    for err in e.errors():
        loc = [str(x) for x in err["loc"]]
        component = loc[1] if len(loc) > 1 and loc[0] == "components" else None
        errors.append(ConfigError(code="grammar", message=err["msg"], component=component, path=".".join(loc) or None))
    return errors


def _err(errors: list[ConfigError], code: str, message: str, component: str | None = None, path: str | None = None) -> None:
    errors.append(ConfigError(code=code, message=message, component=component, path=path))


def _build_nodes(spec: TelescopeOpticsSpec, registry: KindRegistry, errors: list[ConfigError]) -> dict[str, Node]:
    components = spec.components
    kinds: dict[str, Kind] = {}
    for name, comp in components.items():
        kind = registry.resolve(comp)
        if kind is None:
            if comp.optics is not None or comp.paths is not None:
                _err(errors, "unknown_kind", f"{name}: kind {comp.kind!r} is not an optical kind, yet it carries optics/paths", name)
            continue
        kinds[name] = kind

    referenced: dict[str, list[str]] = defaultdict(list)
    for name, comp in components.items():
        if name in kinds and comp.optics is not None:
            for ref in comp.optics.edges():
                referenced[ref.component].append(name)

    included: dict[str, Kind] = {}
    for name, kind in kinds.items():
        comp = components[name]
        if isinstance(kind, Source):
            if comp.optics is not None:
                _err(errors, "source_has_input", f"{name}: a source ({comp.kind}) has no optical input; drop its optics block", name)
            included[name] = kind
            continue
        if comp.optics is None:
            if comp.paths is not None:
                _err(errors, "paths_without_optics", f"{name}: declares paths but no optics.from — where does its light come from?", name)
            elif name in referenced:
                _err(errors, "missing_input", f"{name}: referenced by {', '.join(referenced[name])} but has no optics block — light from nowhere", name)
            continue
        included[name] = kind

    for name, comp in components.items():
        if name not in kinds and name in referenced:
            who = ", ".join(referenced[name])
            _err(errors, "unknown_kind", f"{who}: reference {name!r} whose kind {comp.kind!r} is not optical", who if "," not in who else None)

    edges: list[Edge] = []
    for name, kind in included.items():
        comp = components[name]
        if comp.optics is None:
            continue
        if comp.optics.is_fan_in and not isinstance(kind, Selector):
            _err(errors, "inputs_on_non_selector", f"{name}: only a selector may declare inputs (kind {comp.kind!r} is {kind.archetype})", name)
            continue
        for ref in comp.optics.edges():
            if ref.component not in components:
                _err(errors, "unknown_component", f"{name}: optics references unknown component {ref.component!r}", name)
                continue
            if ref.component not in included:
                continue  # reported above
            edges.append(Edge(upstream=ref.component, downstream=name, port=ref.port, owner=ref.port_owner))

    nodes: dict[str, Node] = {}
    for name, kind in included.items():
        comp = components[name]
        shape = kind.shape(comp) if isinstance(kind, Selector) else None
        positions = kind.declared_positions(comp)
        if isinstance(kind, Selector) and shape == SelectorShape.FAN_IN and kind.fan_in_positions is not None:
            for pos in comp.optics.inputs or {}:
                if pos not in kind.fan_in_positions:
                    _err(errors, "undeclared_position", f"{name}: kind {comp.kind!r} selects between {sorted(kind.fan_in_positions)}, not {pos!r}", name, f"{name}.optics.inputs.{pos}")
        if isinstance(kind, Selector) and shape == SelectorShape.FAN_IN and comp.positions is not None:
            for pos in comp.optics.inputs or {}:
                if pos not in comp.positions:
                    _err(errors, "undeclared_position", f"{name}: input position {pos!r} is not among its declared positions", name, f"{name}.optics.inputs.{pos}")
        nodes[name] = Node(
            name=name,
            kind=kind,
            spec=comp,
            shape=shape,
            positions=positions,
            inputs=tuple(e for e in edges if e.downstream == name),
            outputs=tuple(e for e in edges if e.upstream == name),
        )

    for edge in edges:
        _check_edge(edge, nodes, errors)
    _check_duplicates(edges, nodes, errors)
    return nodes


def _check_edge(edge: Edge, nodes: dict[str, Node], errors: list[ConfigError]) -> None:
    up, down = nodes[edge.upstream], nodes[edge.downstream]
    path = f"{down.name}.optics"
    if up.archetype == Archetype.DETECTOR:
        _err(errors, "detector_as_upstream", f"{down.name}: hangs on {up.name!r}, but a detector emits no light", down.name, path)
        return
    if edge.owner == PortOwner.SELF:
        # fan-in takes the upstream's *single* output; a multi-output selector cannot be named without its port
        if up.archetype == Archetype.SELECTOR and up.shape == SelectorShape.OUTPUT_PORTS:
            _err(errors, "port_required", f"{down.name}: inputs reference {up.name!r}, which switches between output ports; hang a passive on the port (`from: {{{up.name}: <symbol>}}`) and reference that", down.name, path)
        return
    if edge.owner == PortOwner.UPSTREAM:
        if up.archetype == Archetype.SELECTOR and up.shape == SelectorShape.OUTPUT_PORTS:
            if up.positions is None:
                _err(errors, "undeclared_port", f"{down.name}: references port {edge.port!r} of {up.name!r}, which declares no positions", down.name, path)
            elif edge.port not in up.positions:
                _err(errors, "undeclared_port", f"{down.name}: {up.name!r} has no port {edge.port!r} (declared: {', '.join(sorted(up.positions))})", down.name, path)
            return
        if up.archetype == Archetype.SPLITTER:
            if up.positions is not None and edge.port not in up.positions:
                _err(errors, "undeclared_port", f"{down.name}: splitter {up.name!r} has no port {edge.port!r}", down.name, path)
            return
        if up.archetype == Archetype.SELECTOR:
            _err(errors, "no_output_ports", f"{down.name}: {up.name!r} has a single output ({up.shape.value} selector); use `from: {up.name}`", down.name, path)
            return
        _err(errors, "not_a_switch", f"{down.name}: {up.name!r} ({up.archetype}) has no ports; use `from: {up.name}`", down.name, path)
        return
    # passive edge
    if up.archetype == Archetype.SELECTOR and up.shape == SelectorShape.OUTPUT_PORTS:
        _err(errors, "port_required", f"{down.name}: {up.name!r} switches between output ports; say which one: `from: {{{up.name}: <symbol>}}`", down.name, path)


def _check_duplicates(edges: list[Edge], nodes: dict[str, Node], errors: list[ConfigError]) -> None:
    by_output: dict[tuple[str, str | None], list[str]] = defaultdict(list)
    for e in edges:
        by_output[(e.upstream, e.upstream_port)].append(e.downstream)
    for (upstream, port), downs in by_output.items():
        if len(downs) < 2 or nodes[upstream].archetype in (Archetype.SPLITTER, Archetype.SOURCE):
            continue  # a splitter feeds several outputs by design; a source (sky, screen) is ambient light
        where = f"{upstream}:{port}" if port else upstream
        for d in downs:
            _err(errors, "duplicate_from", f"{d}: {', '.join(downs)} all hang on {where} — one output feeds one component; insert a splitter", d, f"{d}.optics")


def _check_cycles(graph: OpticalGraph, errors: list[ConfigError]) -> None:
    """Report every cycle (one error per back edge found by the DFS), not just the first."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in graph.nodes}

    def visit(name: str, trail: list[str]) -> None:
        colour[name] = GREY
        for e in graph.nodes[name].inputs:
            if e.upstream not in colour:
                continue  # dangling reference, reported structurally
            if colour[e.upstream] == GREY:
                cycle = trail[trail.index(e.upstream):] + [e.upstream] if e.upstream in trail else [e.upstream, name, e.upstream]
                _err(errors, "cycle", f"optical cycle: {' -> '.join(cycle)}", name)
            elif colour[e.upstream] == WHITE:
                visit(e.upstream, trail + [e.upstream])
        colour[name] = BLACK

    for name in graph.nodes:
        if colour[name] == WHITE:
            visit(name, [name])


def _check_paths(graph: OpticalGraph, errors: list[ConfigError]) -> None:
    from obcom.optics.routes import enumerate_routes, routes_for_goal

    all_classes = graph.possible_classes(graph.nodes) | {DARK}
    for node in graph.nodes.values():
        if node.paths is None:
            continue
        if node.archetype != Archetype.DETECTOR:
            _err(errors, "paths_on_non_detector", f"{node.name}: only detectors declare paths (kind {node.spec.kind!r} is {node.archetype})", node.name, f"{node.name}.paths")
            continue
        cone = graph.upstream_cone(node.name)
        cone_classes = graph.possible_classes(cone) | {DARK}
        routes = enumerate_routes(graph, node.name)
        for function in node.paths:
            for i, alt in enumerate(node.paths.alternatives(function)):
                where = f"{node.name}.paths.{function}[{i}]"
                ok = True
                if alt.see not in cone_classes:
                    _err(errors, "unsatisfiable_path", f"{where}: nothing upstream of {node.name} can emit {alt.see!r}", node.name, where)
                    ok = False
                if alt.when is not None and alt.when not in all_classes:
                    _err(errors, "unknown_light_class", f"{where}: `when: {alt.when}` names a class no source of this telescope emits", node.name, where)
                for key, symbol in alt.via.items():
                    component, aspect = split_state_key(key)
                    if component not in cone or graph.nodes[component].archetype != Archetype.SELECTOR:
                        _err(errors, "via_outside_cone", f"{where}: via {key!r} is not a selector upstream of {node.name}", node.name, f"{where}.via")
                        ok = False
                        continue
                    sel = graph.nodes[component]
                    if aspect is not None:
                        asp = sel.kind.aspect(aspect) if isinstance(sel.kind, Selector) else None
                        if asp is None:
                            _err(errors, "unknown_aspect", f"{where}: {component!r} has no aspect {aspect!r}", node.name, f"{where}.via")
                            ok = False
                        elif symbol not in asp.positions:
                            _err(errors, "undeclared_position", f"{where}: {key!r} is {asp.on!r} or {asp.off!r}, not {symbol!r}", node.name, f"{where}.via")
                            ok = False
                    elif sel.positions is None or symbol not in sel.positions:
                        _err(errors, "undeclared_position", f"{where}: {component!r} has no position {symbol!r}", node.name, f"{where}.via")
                        ok = False
                if ok and not routes_for_goal(routes, alt):
                    _err(errors, "unsatisfiable_path", f"{where}: no route through the graph shows {node.name} {alt.see!r} with via {alt.via}", node.name, where)
