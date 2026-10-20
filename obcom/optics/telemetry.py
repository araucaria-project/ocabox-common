"""From device readbacks to a :class:`ProvenState` — the one place that knows what an Alpaca code
means for the optical model. Pure functions; the caller (the CLI today, TIC's optics adapter in
D-F1) gathers the readings and hands them over. Every mapping errs towards *undefined*: a code
that does not prove a position proves nothing.

Alpaca vocabularies (ASCOM ICoverCalibratorV1, IDomeV2):

===============  ==================================================  ==========================
device           code                                                optical meaning
===============  ==================================================  ==========================
CoverStatus      0 NotPresent · 1 Closed · 2 Moving · 3 Open · 4 Unknown · 5 Error   ``close`` / moving / ``open`` / undefined
CalibratorStatus 0 NotPresent · 1 Off · 2 NotReady · 3 Ready · 4 Unknown · 5 Error  ``off`` (no lamp is an off lamp) / ``off`` / moving / ``on`` / undefined
ShutterState     0 Open · 1 Closed · 2 Opening · 3 Closing · 4 Error                  shutter open True / False / moving / undefined
===============  ==================================================  ==========================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from datamodels.optics import SelectorState, state_key

from obcom.optics.graph import OpticalGraph
from obcom.optics.state import ProvenState

COVER_STATUS = {1: "close", 3: "open"}  #: CoverStatus → cover position; 2 is moving, the rest undefined
CALIBRATOR_STATUS = {0: "off", 1: "off", 3: "on"}  #: CalibratorStatus → calibrator aspect; 2 is moving, the rest undefined
SHUTTER_STATE = {0: True, 1: False}  #: ShutterState → shutter open; 2/3 are moving, 4 undefined

#: Alpaca code names, for telling people why a code proves nothing
COVER_STATUS_NAMES = {0: "NotPresent", 1: "Closed", 2: "Moving", 3: "Open", 4: "Unknown", 5: "Error"}
CALIBRATOR_STATUS_NAMES = {0: "NotPresent", 1: "Off", 2: "NotReady", 3: "Ready", 4: "Unknown", 5: "Error"}
SHUTTER_STATE_NAMES = {0: "Open", 1: "Closed", 2: "Opening", 3: "Closing", 4: "Error"}


def cover_cause(code: int) -> str | None:
    """Why a CoverStatus proves no cover position (``None`` when it does, or is plain moving)."""
    return None if code in COVER_STATUS or code == 2 else _reports("cover", code, COVER_STATUS_NAMES, "CoverStatus")


def calibrator_cause(code: int) -> str | None:
    """Why a CalibratorStatus proves no lamp state (``None`` when it does, or is plain moving)."""
    return None if code in CALIBRATOR_STATUS or code == 2 else _reports("calibrator", code, CALIBRATOR_STATUS_NAMES, "CalibratorStatus")


def shutter_cause(code: int) -> str | None:
    """Why a ShutterState proves no shutter state — the cause of an undefined dome."""
    return None if code in SHUTTER_STATE else _reports("shutter", code, SHUTTER_STATE_NAMES, "ShutterState")


def _reports(device: str, code: int, names: Mapping[int, str], field_name: str) -> str:
    return f"{device} reports {names.get(code, 'an unknown code')} ({field_name} {code})"


@dataclass(frozen=True)
class Readings:
    """Raw device readbacks for one telescope, as the devices report them. ``None`` = not read.
    ``tertiary_port`` is the physical nasmyth port number; ``lamps`` maps a lamp component to its
    reported power (``True`` lit, ``False`` off); ``positions`` maps other selectors already
    resolved to symbols by their own driver (BESO's M4/M5)."""

    tertiary_port: int | None = None
    tertiary_moving: bool = False
    cover_status: int | None = None
    calibrator_status: int | None = None
    shutter_state: int | None = None
    dome_az_deg: float | None = None
    mount_az_deg: float | None = None
    mount_alt_deg: float | None = None
    sun_alt_deg: float | None = None
    lamps: Mapping[str, bool | None] = field(default_factory=dict)
    positions: Mapping[str, str | None] = field(default_factory=dict)


def tertiary_symbol(graph: OpticalGraph, port: int | None) -> str | None:
    """The ``positions:`` symbol whose ``port`` is the reported nasmyth port; ``None`` when the
    readback maps to no declared symbol (oca-problems#107 — exactly the case the operator needs
    reported as undefined)."""
    if port is None or "tertiary" not in graph.nodes:
        return None
    positions = graph.nodes["tertiary"].spec.positions
    if positions is None:
        return None
    return next((symbol for symbol in positions if positions[symbol].port == port), None)


def proven_state(graph: OpticalGraph, readings: Readings, overrides: Mapping[str, str | None] | None = None) -> ProvenState:
    """Build the proven state of ``graph``'s telescope from ``readings``. A device that was not read
    contributes nothing (its axis stays undefined); a device caught moving is reported moving;
    ``overrides`` (``{state key: symbol}``) are assumed over whatever the devices say or the
    environment derives (:attr:`ProvenState.assumed`) — a tool's what if, never a service's."""
    selectors: dict[str, SelectorState] = {}
    if "tertiary" in graph.nodes and (readings.tertiary_port is not None or readings.tertiary_moving):
        selectors["tertiary"] = SelectorState(position=tertiary_symbol(graph, readings.tertiary_port), moving=readings.tertiary_moving, raw=readings.tertiary_port)
    if "covercalibrator" in graph.nodes:
        if readings.cover_status is not None:
            selectors["covercalibrator"] = SelectorState(position=COVER_STATUS.get(readings.cover_status), moving=readings.cover_status == 2, raw=readings.cover_status)
        if readings.calibrator_status is not None:
            selectors[state_key("covercalibrator", "calibrator")] = SelectorState(
                position=CALIBRATOR_STATUS.get(readings.calibrator_status), moving=readings.calibrator_status == 2, raw=readings.calibrator_status
            )
    for lamp, lit in readings.lamps.items():
        if lamp in graph.nodes:
            selectors[lamp] = SelectorState(position=None if lit is None else ("on" if lit else "off"), raw=lit)
    for component, symbol in readings.positions.items():
        if component in graph.nodes:
            selectors[component] = SelectorState(position=symbol)

    causes: dict[str, str] = {}
    for key, code, why in (("covercalibrator", readings.cover_status, cover_cause),
                           (state_key("covercalibrator", "calibrator"), readings.calibrator_status, calibrator_cause),
                           ("dome", readings.shutter_state, shutter_cause)):
        if code is not None and key.partition(".")[0] in graph.nodes and (cause := why(code)) is not None:
            causes[key] = cause

    shutter = SHUTTER_STATE.get(readings.shutter_state) if readings.shutter_state is not None else None
    environment = {
        k: v
        for k, v in {
            "sun_alt_deg": readings.sun_alt_deg,
            "dome_shutter_open": shutter,
            "dome_az_deg": readings.dome_az_deg,
            "mount_az_deg": readings.mount_az_deg,
            "mount_alt_deg": readings.mount_alt_deg,
        }.items()
        if v is not None
    }
    return ProvenState.build({k: s.position for k, s in selectors.items()}, moving=[k for k, s in selectors.items() if s.moving],
                             causes=causes, assumed=overrides, **environment)
