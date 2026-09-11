"""The optical graph: ``parse_graph`` turns a telescope's ``components:`` into validated nodes and
feeds, or refuses with every reason at once (``GraphInvalid`` → the ``Invalid`` verdict).

A node is a component with its kind contract; a *feed* is light arriving at one of the node's
input ports from a set of output ports of an upstream component (``from: X``, ``from: {X: p}``,
``from: {X: [p1, p2]}``, ``inputs: {pos: X}`` all normalise to feeds). Everything a config typo
can express is caught here, at load time: unknown kinds and components, references to undeclared
ports, two components hanging on one port without a splitter, ports on components that have
none, cycles, ``paths`` goals no source can ever satisfy, ``via`` outside the detector's upstream
cone, malformed kind options. Runtime state never enters this module.

Reporting: structural errors and cycles are collected together and raised at once; ``paths``
validation needs a structurally sound, acyclic graph (it enumerates routes) and therefore runs
only when the first pass found nothing — its errors are then reported all at once as well.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import product
from typing import Iterable, Iterator, Mapping

from datamodels.optics import (
    DARK,
    UNDEFINED,
    Archetype,
    ConfigError,
    DetectorPaths,
    Invalid,
    OpticalComponentSpec,
    PortOwner,
    TelescopeOpticsSpec,
    VerdictKind,
    split_state_key,
)
from pydantic import ValidationError

from obcom.optics.kinds import (
    Aspect,
    Axis,
    AxisValues,
    DEFAULT_REGISTRY,
    Emit,
    IN,
    Kind,
    KindRegistry,
    OUT,
    position_flag_problems,
    Selector,
    SelectorShape,
    Signals,
    Source,
    Transmit,
)


class GraphInvalid(ValueError):
    """The authored graph is not usable. Carries every ``ConfigError`` found, not just the first."""

    def __init__(self, errors: Iterable[ConfigError]):
        self.errors: list[ConfigError] = list(errors)
        super().__init__("; ".join(f"[{e.code}] {e.message}" for e in self.errors))

    @property
    def verdict(self) -> Invalid:
        return Invalid(kind=VerdictKind.INVALID, errors=self.errors)


@dataclass(frozen=True)
class Feed:
    """Light arriving at one input port of a node from ``upstream``'s output ``ports``: one edge,
    live on any of the ports (``from: {X: [p1, p2]}``); ``{OUT}`` for a single-output upstream."""

    upstream: str
    ports: frozenset[str]


@dataclass(frozen=True)
class Node:
    name: str
    kind: Kind
    spec: OpticalComponentSpec
    feeds: Mapping[str, tuple[Feed, ...]] = field(default_factory=dict)  #: input port → feeds

    @property
    def archetype(self) -> Archetype:
        return self.kind.archetype

    @property
    def inputs(self) -> frozenset[str]:
        return self.kind.inputs(self.spec)

    @property
    def outputs(self) -> frozenset[str] | None:
        return self.kind.outputs(self.spec)

    @property
    def axes(self) -> tuple[Axis, ...]:
        return self.kind.axes(self.spec)

    @property
    def primary(self) -> Axis | None:
        return next((a for a in self.axes if a.name is None), None)

    @property
    def aspects(self) -> tuple[Aspect, ...]:
        return self.kind.aspects if isinstance(self.kind, Selector) else ()

    @property
    def positions(self) -> frozenset[str] | None:
        """Declared position vocabulary: a selector's primary axis, a splitter's declared ports."""
        if self.archetype == Archetype.SELECTOR:
            return frozenset(self.primary.vocabulary) if self.primary is not None else frozenset()
        if self.archetype == Archetype.SPLITTER:
            return self.outputs
        return None

    @property
    def shape(self) -> SelectorShape | None:
        return self.kind.shape(self.spec) if isinstance(self.kind, Selector) else None

    @property
    def upstreams(self) -> frozenset[str]:
        return frozenset(f.upstream for feeds in self.feeds.values() for f in feeds)

    @property
    def fan_in(self) -> dict[str, str]:
        """Fan-in selectors: input position → upstream component."""
        return {port: feeds[0].upstream for port, feeds in self.feeds.items() if port != IN and feeds}

    @property
    def paths(self) -> DetectorPaths | None:
        return self.spec.paths

    def table(self, values: AxisValues) -> dict[str, Signals]:
        return self.kind.transfer(self.spec, values)

    def assignments(self) -> Iterator[dict[str | None, str]]:
        """Every combination of the actuated axes' vocabularies, in authored order (one empty
        assignment when there are none) — the rows of the transfer table that routes enumerate."""
        axes = [a for a in self.axes if a.actuated]
        for combo in product(*(a.vocabulary for a in axes)):
            yield {a.name: v for a, v in zip(axes, combo)}

    def transmitting_positions(self) -> frozenset[str]:
        """Primary positions in which light can pass through the component (for some value of
        the other axes)."""
        return frozenset(
            values[None]
            for values in self.assignments()
            if None in values and any(isinstance(s, Transmit) for sigs in self.table(values).values() for s in sigs)
        )

    def dark_positions(self) -> frozenset[str]:
        return (self.positions or frozenset()) - self.transmitting_positions()


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
        stack = list(self.node(name).upstreams)
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.nodes[current].upstreams)
        return frozenset(seen)

    def emitted_classes(self, name: str) -> frozenset[str]:
        """Every light class the component can ever put on a path (its own emissions, reserved
        classes excluded)."""
        node = self.nodes[name]
        if isinstance(node.kind, Source):
            return node.kind.possible_classes(node.spec)
        classes: set[str] = set()
        for values in node.assignments():
            for signals in node.table(values).values():
                classes |= {s.light for s in signals if isinstance(s, Emit)}
        return frozenset(classes - {DARK, UNDEFINED})

    def possible_classes(self, names: Iterable[str]) -> frozenset[str]:
        classes: set[str] = set()
        for name in names:
            classes |= self.emitted_classes(name)
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
    _check_cycles(graph, errors)  # safe on a partially valid graph: every structural and cyclic problem at once
    if errors:
        raise GraphInvalid(errors)
    _check_paths(graph, errors)
    _check_presets(graph, errors)
    if errors:
        raise GraphInvalid(errors)
    return graph


# --- internals ---------------------------------------------------------------------------------


def _validate_shape(components, presets) -> TelescopeOpticsSpec:
    if isinstance(components, TelescopeOpticsSpec):
        if presets is not None:
            raise ValueError("presets travel inside a TelescopeOpticsSpec; pass either the spec or (components, presets)")
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

    nodes: dict[str, Node] = {}
    for name, kind in included.items():
        comp = components[name]
        feeds: dict[str, list[Feed]] = defaultdict(list)
        if comp.optics is not None:
            if comp.optics.is_fan_in and not isinstance(kind, Selector):
                _err(errors, "inputs_on_non_selector", f"{name}: only a selector may declare inputs (kind {comp.kind!r} is {kind.archetype})", name)
            else:
                ports_of: dict[str, set[str]] = defaultdict(set)
                for ref in comp.optics.edges():
                    if ref.component not in components:
                        _err(errors, "unknown_component", f"{name}: optics references unknown component {ref.component!r}", name)
                        continue
                    if ref.component not in included:
                        continue  # reported above
                    if ref.port_owner == PortOwner.SELF:
                        feeds[ref.port].append(Feed(ref.component, frozenset({OUT})))
                    elif ref.port_owner == PortOwner.UPSTREAM:
                        ports_of[ref.component].add(ref.port)
                    else:
                        feeds[IN].append(Feed(ref.component, frozenset({OUT})))
                for upstream, ports in ports_of.items():
                    feeds[IN].append(Feed(upstream, frozenset(ports)))
            if isinstance(kind, Selector) and comp.optics.is_fan_in:
                for pos in comp.optics.inputs or {}:
                    if kind.fan_in_positions is not None and pos not in kind.fan_in_positions:
                        _err(errors, "undeclared_position", f"{name}: kind {comp.kind!r} selects between {sorted(kind.fan_in_positions)}, not {pos!r}", name, f"{name}.optics.inputs.{pos}")
                    elif comp.positions is not None and pos not in comp.positions:
                        _err(errors, "undeclared_position", f"{name}: input position {pos!r} is not among its declared positions", name, f"{name}.optics.inputs.{pos}")
        for problem in (*kind.validate(comp, components), *position_flag_problems(comp)):
            _err(errors, "invalid_option", f"{name}: {problem}", name, name)
        nodes[name] = Node(name=name, kind=kind, spec=comp, feeds={port: tuple(fs) for port, fs in feeds.items()})

    for node in nodes.values():
        for in_port, feeds in node.feeds.items():
            for feed in feeds:
                _check_feed(node, in_port, feed, nodes, errors)
    _check_duplicates(nodes, errors)
    return nodes


def _check_feed(node: Node, in_port: str, feed: Feed, nodes: dict[str, Node], errors: list[ConfigError]) -> None:
    up = nodes[feed.upstream]
    path = f"{node.name}.optics"
    if up.archetype == Archetype.DETECTOR:
        _err(errors, "detector_as_upstream", f"{node.name}: hangs on {up.name!r}, but a detector emits no light", node.name, path)
        return
    outs = up.outputs
    if outs is None:
        return  # a splitter feeds whatever port a downstream names
    if outs == frozenset({OUT}):
        if feed.ports != frozenset({OUT}):
            if up.archetype == Archetype.SELECTOR:
                _err(errors, "no_output_ports", f"{node.name}: {up.name!r} has a single output ({up.shape.value} selector); use `from: {up.name}`", node.name, path)
            else:
                _err(errors, "not_a_switch", f"{node.name}: {up.name!r} ({up.archetype}) has no ports; use `from: {up.name}`", node.name, path)
        return
    # a switch between output ports (M3): the feed must name which
    if feed.ports == frozenset({OUT}):
        if in_port == IN:
            _err(errors, "port_required", f"{node.name}: {up.name!r} switches between output ports; say which one: `from: {{{up.name}: <symbol>}}`", node.name, path)
        else:
            _err(errors, "port_required", f"{node.name}: inputs reference {up.name!r}, which switches between output ports; hang a passive on the port (`from: {{{up.name}: <symbol>}}`) and reference that", node.name, path)
        return
    for port in sorted(feed.ports - outs):
        if not outs:
            _err(errors, "undeclared_port", f"{node.name}: references port {port!r} of {up.name!r}, which declares no positions", node.name, path)
        else:
            _err(errors, "undeclared_port", f"{node.name}: {up.name!r} has no port {port!r} (declared: {', '.join(sorted(outs))})", node.name, path)


def _check_duplicates(nodes: dict[str, Node], errors: list[ConfigError]) -> None:
    by_output: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node in nodes.values():
        for feeds in node.feeds.values():
            for feed in feeds:
                for port in feed.ports:
                    by_output[(feed.upstream, port)].append(node.name)
    for (upstream, port), downs in by_output.items():
        up = nodes[upstream]
        if len(downs) < 2 or up.archetype == Archetype.SPLITTER or (isinstance(up.kind, Source) and up.kind.ambient(up.spec)):
            continue  # a splitter feeds several outputs by design; ambient light (sky, screen) fills every aperture
        where = upstream if port == OUT else f"{upstream}:{port}"
        for d in downs:
            _err(errors, "duplicate_from", f"{d}: {', '.join(downs)} all hang on {where} — one output feeds one component; insert a splitter", d, f"{d}.optics")


def _check_cycles(graph: OpticalGraph, errors: list[ConfigError]) -> None:
    """Report every cycle (one error per back edge found by the DFS), not just the first."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in graph.nodes}

    def visit(name: str, trail: list[str]) -> None:
        colour[name] = GREY
        for upstream in sorted(graph.nodes[name].upstreams):
            if upstream not in colour:
                continue  # dangling reference, reported structurally
            if colour[upstream] == GREY:
                cycle = trail[trail.index(upstream):] + [upstream] if upstream in trail else [upstream, name, upstream]
                _err(errors, "cycle", f"optical cycle: {' -> '.join(cycle)}", name)
            elif colour[upstream] == WHITE:
                visit(upstream, trail + [upstream])
        colour[name] = BLACK

    for name in graph.nodes:
        if colour[name] == WHITE:
            visit(name, [name])


def _check_presets(graph: OpticalGraph, errors: list[ConfigError]) -> None:
    """``presets: {name: {detector: function}}`` is sugar over declared paths: every entry must name
    a detector of this graph and one of its declared functions (a shape-only concern in datamodels)."""
    for preset, entries in graph.presets.items():
        for detector, function in entries.items():
            where = f"presets.{preset}.{detector}"
            node = graph.nodes.get(detector)
            if node is None or node.archetype != Archetype.DETECTOR:
                _err(errors, "preset_unknown_detector", f"{where}: {detector!r} is not a detector of this telescope", None, where)
            elif node.paths is None or function not in node.paths:
                declared = ", ".join(sorted(node.paths)) if node.paths is not None else "none"
                _err(errors, "preset_unknown_path", f"{where}: {detector!r} declares no path {function!r} (declared: {declared})", detector, where)


def _check_paths(graph: OpticalGraph, errors: list[ConfigError]) -> None:
    from obcom.optics.routes import enumerate_routes, routes_for_goal

    light_classes = graph.possible_classes(graph.nodes) - {DARK, UNDEFINED}  # what `when:` is judged against: light, never dark
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
                if alt.when is not None and alt.when not in light_classes:
                    _err(errors, "unknown_light_class", f"{where}: `when: {alt.when}` names no light any source of this telescope emits (dark is not light)", node.name, where)
                for key, symbol in alt.via.items():
                    component, aspect = split_state_key(key)
                    if component not in cone or graph.nodes[component].archetype != Archetype.SELECTOR:
                        _err(errors, "via_outside_cone", f"{where}: via {key!r} is not a selector upstream of {node.name}", node.name, f"{where}.via")
                        ok = False
                        continue
                    sel = graph.nodes[component]
                    axis = next((a for a in sel.axes if a.name == aspect), None)
                    if axis is None:
                        _err(errors, "unknown_aspect", f"{where}: {component!r} has no aspect {aspect!r}", node.name, f"{where}.via")
                        ok = False
                    elif symbol not in axis.vocabulary:
                        what = key if aspect else component
                        _err(errors, "undeclared_position", f"{where}: {what!r} has no position {symbol!r} (declared: {', '.join(axis.vocabulary)})", node.name, f"{where}.via")
                        ok = False
                if ok and not routes_for_goal(routes, alt):
                    _err(errors, "unsatisfiable_path", f"{where}: no route through the graph shows {node.name} {alt.see!r} with via {alt.via}", node.name, where)
