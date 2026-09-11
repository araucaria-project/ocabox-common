"""``check`` and ``resolve``: paths-as-goals judged against the proven state.

``check(detector, function)`` walks the function's alternatives in authored order and answers
with the first that applies: ``active`` (the detector sees exactly the goal now), ``settable``
(these selector moves get there), ``collision`` (a needed selector is held elsewhere),
``impossible`` (no source provides the goal now). ``invalid`` never comes from here — the graph
would not have loaded.

Among several settable routes the solver prefers, in order: fewest moves, least collateral
change to what the *other* detectors see, the blocking/emitting terminal closest to the
detector, authored order. Operational policy beyond that (never close the dome for a dark) is
authored with ``via`` or presets, not guessed here.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from datamodels.optics import (
    DARK,
    UNDEFINED,
    Active,
    CheckResult,
    Collision,
    GoalSpec,
    Impossible,
    SeesRecord,
    Settable,
    Verdict,
    VerdictKind,
    split_state_key,
)

from obcom.optics.graph import OpticalGraph
from obcom.optics.kinds import Source
from obcom.optics.routes import StaticRoute, enumerate_routes, routes_for_goal
from obcom.optics.sees import available_classes, sees, source_emission
from obcom.optics.state import ProvenState, resolve_axes


class UnknownFunction(LookupError):
    """The detector declares no such path — a caller error, not a config error."""


def check(graph: OpticalGraph, state: ProvenState, detector: str, function: str) -> Verdict:
    node = graph.node(detector)
    if node.paths is None or function not in node.paths:
        declared = sorted(node.paths) if node.paths is not None else []
        raise UnknownFunction(f"{detector} declares no path {function!r} (declared: {declared})")

    now = sees(graph, state, detector)
    available = available_classes(graph, state)
    routes = enumerate_routes(graph, detector)
    alternatives = node.paths.alternatives(function)
    proven = _proven_map(graph, state, _keys(routes, alternatives))
    others = tuple(d for d in graph.detectors if d != detector)
    others_now = {d: sees(graph, state, d) for d in others}

    unavailable: str | None = None
    not_emitting: set[str] = set()
    undefined_sources: set[str] = set()
    collision: Collision | None = None
    applicable = False
    for alt in alternatives:
        if alt.when is not None and alt.when not in available:
            continue
        applicable = True
        if _is_active(now, alt, proven):
            on_path = {c for r in now for c in (r.terminal, *r.via)}
            positions = {k: v for k, v in proven.items() if v is not None and split_state_key(k)[0] in on_path}
            return Active(kind=VerdictKind.ACTIVE, see=alt.see, positions=positions)

        feasible: list[tuple[tuple[int, int, int, int], StaticRoute, dict[str, str]]] = []
        for index, route in enumerate(routes_for_goal(routes, alt)):
            emitted = _terminal_emission(graph, state, route)
            if emitted is not None and emitted != alt.see:
                unavailable = unavailable or alt.see
                not_emitting.add(route.terminal)
                if emitted == UNDEFINED:
                    undefined_sources.add(route.terminal)
                continue
            moves = route.moves_from(proven)
            held = _collision(state, route, moves, alt.see)
            if held is not None:
                collision = collision or held
                continue
            collateral = _collateral(graph, state, others_now, route)
            feasible.append(((len(moves), collateral, len(route.via), index), route, moves))
        if feasible:
            _, route, moves = min(feasible, key=lambda t: t[0])
            return Settable(kind=VerdictKind.SETTABLE, see=alt.see, positions=dict(route.positions), moves=moves)

    if collision is not None:
        return collision
    undefined_at = tuple(sorted({k for k, v in proven.items() if v is None} | {r.terminal for r in now if r.light_class == UNDEFINED} | undefined_sources))
    have = ", ".join(sorted(available)) or "nothing"
    if not applicable:
        reason = f"no alternative of {detector}.{function} applies now (when-conditions unmet; available: {have})"
    elif unavailable is not None:
        who = ", ".join(sorted(not_emitting)) or "no source"
        reason = f"{who} not providing {unavailable!r} now (available: {have})"
    else:
        reason = f"no route to {detector}.{function} can be set now"
    if undefined_at:
        reason += f"; undefined: {', '.join(undefined_at)}"
    return Impossible(kind=VerdictKind.IMPOSSIBLE, reason=reason, unavailable=unavailable, undefined_at=undefined_at)


def check_result(graph: OpticalGraph, state: ProvenState, detector: str, function: str) -> CheckResult:
    return CheckResult(detector=detector, function=function, verdict=check(graph, state, detector, function))


def resolve(graph: OpticalGraph, state: ProvenState, detector: str, function: str) -> dict[str, str] | None:
    """The selector positions that realise ``function`` on ``detector`` — the proven ones when
    already active, the target ones when settable; ``None`` when neither."""
    verdict = check(graph, state, detector, function)
    if isinstance(verdict, (Active, Settable)):
        return dict(verdict.positions)
    return None


# --- internals ---------------------------------------------------------------------------------


def _keys(routes: Iterable[StaticRoute], alternatives: Iterable[GoalSpec]) -> set[str]:
    keys: set[str] = set()
    for r in routes:
        keys |= set(r.positions)
    for alt in alternatives:
        keys |= set(alt.via)
    return keys


def _proven_map(graph: OpticalGraph, state: ProvenState, keys: Iterable[str]) -> dict[str, str | None]:
    """StateKey → current axis value, ``None`` for undefined."""
    result: dict[str, str | None] = {}
    for key in sorted(keys):
        component, aspect = split_state_key(key)
        resolved = resolve_axes(graph, state, graph.nodes[component]).get(aspect)
        result[key] = None if resolved is None else resolved.value
    return result


def _collateral(graph: OpticalGraph, state: ProvenState, others_now: Mapping[str, frozenset[SeesRecord]], route: StaticRoute) -> int:
    """How many other detectors would see something else after setting ``route`` — whole records,
    so a changed terminal or a second same-class lamp counts as a change."""
    after = state.with_positions(dict(route.positions))
    return sum(1 for detector, before in others_now.items() if sees(graph, after, detector) != before)


def _is_active(now: frozenset[SeesRecord], alt: GoalSpec, proven: Mapping[str, str | None]) -> bool:
    """Active = the detector sees the goal class from *one* terminal and nothing else, through the
    pinned ``via`` — two lamps of the same class are contamination, as in route enumeration."""
    if len(now) != 1 or any(r.light_class != alt.see for r in now):
        return False
    for record in now:
        on_path = {record.terminal, *record.via}
        for key, symbol in alt.via.items():
            if split_state_key(key)[0] not in on_path or proven.get(key) != symbol:
                return False
    return True


def _terminal_emission(graph: OpticalGraph, state: ProvenState, route: StaticRoute) -> str | None:
    """What the route's terminal puts out *now* when it is a source (judged like a selector:
    unusable telemetry ⇒ ``undefined``); ``None`` when the terminal is a selector, whose blocking or
    emitting aspect the route itself sets."""
    terminal = graph.nodes[route.terminal]
    if isinstance(terminal.kind, Source):
        return source_emission(graph, state, terminal)
    return None


def _collision(state: ProvenState, route: StaticRoute, moves: Mapping[str, str], see: str) -> Collision | None:
    for key, required in moves.items():
        hold = state.holds.get(key)
        if hold is not None and hold.position != required:
            by = f" by {hold.holder}" if hold.holder else ""
            return Collision(
                kind=VerdictKind.COLLISION,
                selector=key,
                required=required,
                held=hold.position,
                holder=hold.holder,
                reason=f"{key} is held at {hold.position!r}{by}; {see!r} needs {required!r}",
            )
    return None
