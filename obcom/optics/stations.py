"""Stations: which detectors the telescope is set up for — by configuration and by the selectors now.

A *routing selector* sends light to different detectors by its position: it has more than one
output (M3: ``andor``, ``beso``). The dome, the covers and the calibration mirrors are selectors too,
but they choose *what* comes in, never *to whom* — one output each. A detector is *selected* when
every routing selector in front of it is at a position whose light reaches it (from the static
routes: the camera and its guider through ``tertiary: andor``, BESO and its guider through
``beso``); a *station* is the detectors selected together — an instrument and its guider, paired by
the graph alone.

The default detector — the instrument tab a client opens — is the selected one with functions
(``paths:``) before the guiders, then in configuration order. Not "the one that sees the sky": with
the covers or the dome closed nobody does, and the telescope is still set up for one station. With
no routing selector (wk06, zb08) every detector is selected; with a routing selector's position not
known nothing is decided (``None``).

Pure functions over an :class:`~obcom.optics.graph.OpticalGraph` and positions keyed by state key
(``tertiary`` → ``andor``; ``None`` or missing = not known) — what the optics service publishes as
``self.selectors`` gives them through :func:`positions_of`.
"""

from __future__ import annotations

from typing import Mapping

from datamodels.optics import Archetype

from obcom.optics.graph import OpticalGraph
from obcom.optics.routes import enumerate_routes


def routing_selectors(graph: OpticalGraph) -> tuple[str, ...]:
    """The selectors that send light to different detectors by their position: more than one output."""
    return tuple(name for name, node in graph.nodes.items()
                 if node.archetype == Archetype.SELECTOR and len(node.outputs or ()) > 1)


def selected_by(graph: OpticalGraph, detector: str) -> dict[str, frozenset[str]]:
    """Routing selector → the positions whose light reaches ``detector`` — only those the light
    passes (M3 turned away is the camera's dark terminal, not its way in)."""
    routing = set(routing_selectors(graph))
    toward: dict[str, set[str]] = {}
    for route in enumerate_routes(graph, detector):
        for name in routing.intersection(route.via):
            toward.setdefault(name, set()).add(route.positions[name])
    return {name: frozenset(positions) for name, positions in sorted(toward.items())}


def station(graph: OpticalGraph, detector: str) -> tuple[str, ...]:
    """The detectors selected together with ``detector`` (itself included), in configuration order:
    the camera and its guider behind M3 at ``andor``."""
    mine = selected_by(graph, detector)
    return tuple(d for d in graph.detectors if selected_by(graph, d) == mine)


def is_selected(graph: OpticalGraph, detector: str, positions: Mapping[str, str | None]) -> bool | None:
    """Every routing selector in front of ``detector`` is at a position that reaches it: ``True``;
    one is elsewhere: ``False``; else one is not known: ``None``."""
    known = True
    for name, toward in selected_by(graph, detector).items():
        position = positions.get(name)
        if position is None:
            known = False
        elif position not in toward:
            return False
    return True if known else None


def default_detector(graph: OpticalGraph, positions: Mapping[str, str | None]) -> str | None:
    """The detector a client shows first: the selected one with functions before the guiders, then
    in configuration order; ``None`` while no detector is known to be selected."""
    selected = [d for d in graph.detectors if is_selected(graph, d, positions) is True]
    if not selected:
        return None
    return min(selected, key=lambda d: (not graph.node(d).paths, graph.detectors.index(d)))


def positions_of(selectors: Mapping[str, Mapping] | None) -> dict[str, str | None]:
    """State key → position from what the optics service publishes as ``self.selectors``."""
    return {key: (entry or {}).get("position") for key, entry in (selectors or {}).items()}
