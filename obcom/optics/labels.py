"""Human names of components and their state axes: the vocabulary of every text a person reads
(verdict reasons, the TOI panel, ``oca optics``). Data keeps machine names (``covercalibrator``,
``covercalibrator.calibrator``); only the words shown change.

A name comes from, in order: the component's ``display.label`` (``display.aspects`` for an
aspect) in the config, the kind's name for it when the component is the only one of its kind
on the telescope ("mirror covers", "calibration lamp", "M3"), else the component's own name made
readable (``guider_beso`` → "guider beso") — two cameras are never both "camera".
"""

from __future__ import annotations

from datamodels.optics import split_state_key

from obcom.optics.graph import OpticalGraph

#: what people call a component of a kind, keyed by the kind as written in the config
KIND_LABELS: dict[str, str] = {
    "sky": "sky",
    "flatscreen": "flat screen",
    "lamp": "lamp",
    "beamdump": "beam dump",
    "dome": "dome",
    "covercalibrator": "mirror covers",
    "tertiaryOCA": "M3",
    "tertiary": "M3",
    "mirror": "mirror",
    "splitter": "beam splitter",
    "rotator": "derotator",
    "fiber": "fiber",
    "filterwheel": "filter wheel",
    "camera": "camera",
    "spectrograph": "spectrograph",
}

#: what people call an aspect of a kind
ASPECT_LABELS: dict[tuple[str, str], str] = {
    ("covercalibrator", "calibrator"): "calibration lamp",
}


def component_label(graph: OpticalGraph, name: str) -> str:
    """What people call component ``name`` of this telescope."""
    spec = graph.components[name]
    if spec.display is not None and spec.display.label:
        return spec.display.label
    kind_label = KIND_LABELS.get(spec.kind)
    if kind_label is not None and sum(1 for other in graph.components.values() if other.kind == spec.kind) == 1:
        return kind_label
    return name.replace("_", " ")


def label(graph: OpticalGraph, key: str) -> str:
    """What people call the state axis ``key`` (``tertiary``, ``covercalibrator.calibrator``)."""
    component, aspect = split_state_key(key)
    if aspect is None:
        return component_label(graph, component)
    spec = graph.components[component]
    if spec.display is not None and spec.display.aspects and aspect in spec.display.aspects:
        return spec.display.aspects[aspect]
    aspect_label = ASPECT_LABELS.get((spec.kind, aspect))
    return aspect_label if aspect_label is not None else f"{component_label(graph, component)} {aspect}"
