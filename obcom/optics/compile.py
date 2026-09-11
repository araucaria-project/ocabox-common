"""``compile``: the static route table and per-selector conflict map (W3), *generated from the
graph and then verified against it* — every emitted route is replayed through ``sees`` with the
route's own positions as proven state and must show exactly its class. The config repo's CI
commits the result lockfile-style (``generated_from`` = hash of the authored input) and TIC
publishes it verbatim; runtime state never enters the compilate.
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from typing import Mapping

from datamodels.optics import (
    Conflict,
    OpticsCompiled,
    Route,
    RouteKey,
    SkyState,
    SourceFamily,
    TelescopeCompiled,
    TelescopeOpticsSpec,
    light_family,
    light_state,
)

from obcom.optics.graph import OpticalGraph, parse_graph
from obcom.optics.kinds import DEFAULT_REGISTRY, KindRegistry, SkySource
from obcom.optics.routes import enumerate_routes, routes_for_goal
from obcom.optics.sees import sees
from obcom.optics.state import ProvenState


class CompileError(RuntimeError):
    """A generated route does not reproduce on the graph — a solver bug, never a config error."""


def compile_telescope(graph: OpticalGraph) -> TelescopeCompiled:
    routes: list[Route] = []
    for detector in graph.detectors:
        node = graph.nodes[detector]
        if node.paths is None:
            continue
        static = enumerate_routes(graph, detector)
        for function in node.paths:
            for i, alt in enumerate(node.paths.alternatives(function)):
                # one authored alternative may be realised by several physical routes (a bare `dark`
                # is blocked by the dome, the cover or M3): each gets its own ordinal so RouteKey is unique
                for k, r in enumerate(routes_for_goal(static, alt)):
                    routes.append(
                        Route(detector=detector, function=function, alternative=i, realization=k,
                              see=alt.see, positions=dict(r.positions), when=alt.when)
                    )
    for route in routes:
        _verify(graph, route)
    return TelescopeCompiled(
        selectors=list(graph.selectors),
        detectors=list(graph.detectors),
        routes=routes,
        conflicts=_conflicts(routes),
    )


def compile_observatory(
    telescopes: Mapping[str, Mapping | TelescopeOpticsSpec],
    registry: KindRegistry = DEFAULT_REGISTRY,
    generator: str | None = None,
) -> OpticsCompiled:
    """``{telescope: components-or-spec}`` → the whole-observatory compilate."""
    compiled = {}
    for name, components in telescopes.items():
        spec = components if isinstance(components, TelescopeOpticsSpec) else _as_spec(components)
        compiled[name] = compile_telescope(parse_graph(spec, registry=registry, telescope=name))
    return OpticsCompiled(
        generated_from=authored_hash(telescopes),
        generator=generator or default_generator(),
        telescopes=compiled,
    )


def authored_hash(telescopes: Mapping[str, Mapping | TelescopeOpticsSpec]) -> str:
    """``sha256:<hex>`` of the canonical JSON of the authored input — what CI compares to detect drift."""
    canonical = {
        name: (_as_spec(c) if not isinstance(c, TelescopeOpticsSpec) else c).model_dump(by_alias=True, exclude_none=True)
        for name, c in telescopes.items()
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def default_generator() -> str:
    try:
        return f"ocabox-common {version('ocabox-common')}"
    except PackageNotFoundError:
        return "ocabox-common"


# --- internals ---------------------------------------------------------------------------------


def _as_spec(components: Mapping) -> TelescopeOpticsSpec:
    if "components" in components and all(isinstance(v, Mapping) for v in components.values()) and set(components) <= {"components", "presets"}:
        return TelescopeOpticsSpec.model_validate(components)
    return TelescopeOpticsSpec.model_validate({"components": components})


def _conflicts(routes: list[Route]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for i, a in enumerate(routes):
        for b in routes[i + 1:]:
            if a.detector == b.detector:
                continue  # one detector does one thing at a time; only cross-detector routes contend
            for key in sorted(set(a.positions) & set(b.positions)):
                if a.positions[key] != b.positions[key]:
                    conflicts.append(
                        Conflict(
                            selector=key,
                            a=RouteKey(detector=a.detector, function=a.function, alternative=a.alternative, realization=a.realization),
                            b=RouteKey(detector=b.detector, function=b.function, alternative=b.alternative, realization=b.realization),
                            a_requires=a.positions[key],
                            b_requires=b.positions[key],
                            reason=f"{a.detector}.{a.function} needs {key}={a.positions[key]}, {b.detector}.{b.function} needs {key}={b.positions[key]}",
                        )
                    )
    return conflicts


def _sun_alt_for(graph: OpticalGraph, sky_class: str) -> float:
    """A sun altitude that makes the telescope's sky emit ``sky_class`` (thresholds of its sky kind)."""
    sky = next((n for n in graph.nodes.values() if isinstance(n.kind, SkySource)), None)
    science, (flat_lo, flat_hi) = (-18.0, (-15.0, 1.0)) if sky is None else sky.kind.thresholds(sky.spec)
    state = light_state(sky_class)
    if state == SkyState.SCIENCE:
        return science - 10.0
    if state == SkyState.TWILIGHT:
        return (science + flat_lo) / 2.0
    if state == SkyState.FLAT:
        return (flat_lo + flat_hi) / 2.0
    return flat_hi + 10.0


def _verify(graph: OpticalGraph, route: Route) -> None:
    state = ProvenState.build(dict(route.positions))
    if light_family(route.see) == SourceFamily.SKY:
        state = state.with_environment(sun_alt_deg=_sun_alt_for(graph, route.see))
    now = sees(graph, state, route.detector)
    if not now or any(r.light_class != route.see for r in now):
        seen = ", ".join(sorted(f"{r.light_class}@{r.terminal}" for r in now)) or "nothing"
        raise CompileError(
            f"route {route.detector}.{route.function}[{route.alternative}#{route.realization}] with {dict(route.positions)} should show {route.see!r} but sees {seen}"
        )
