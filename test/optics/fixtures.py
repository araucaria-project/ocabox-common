"""Shared graphs and states for the optics tests. The canonical graphs live next to the solver
(``obcom.optics.conformance``) so that the conformance suite and the unit tests agree."""

import copy

from obcom.optics import ProvenState
from obcom.optics.conformance import BESO, JK15, JK15_WITH_MOUNT, TMMT  # noqa: F401  (re-exported)

NIGHT = {"tertiary": "andor", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open"}


def jk15(**overrides) -> dict:
    """A fresh copy of the jk15 sketch, optionally with components replaced/added (``None`` removes)."""
    comps = copy.deepcopy(JK15)
    for name, spec in overrides.items():
        if spec is None:
            comps.pop(name, None)
        else:
            comps[name] = spec
    return comps


def night(sun_alt_deg: float = -30.0, **positions) -> ProvenState:
    """jk15 imaging configuration at astronomical night, with position overrides."""
    return ProvenState.build({**NIGHT, **positions}, sun_alt_deg=sun_alt_deg, dome_shutter_open=True)
