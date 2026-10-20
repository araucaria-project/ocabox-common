"""How the optical path looks — the same in every renderer (TOI, owies, matplotlib reports).

- ``glyphs.svg``: one sprite of component symbols (``<symbol id="m3">``), strokes in
  ``currentColor``; :func:`glyph_svg` cuts one out as a standalone SVG for renderers that take a
  document (``QSvgRenderer``), owies uses the sprite as it is;
- ``tokens.json``: the themes — colours of light classes and states, the line grammar (light,
  dark, unknown, idle), rings; :func:`theme`;
- :func:`glyph_of`: which symbol a component wears — ``display.glyph`` in the config, else its kind's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Mapping

from datamodels.optics import DARK, UNDEFINED

from obcom.optics.graph import OpticalGraph

#: the symbol a component of a kind wears, keyed by the kind as written in the config
KIND_GLYPHS: dict[str, str] = {
    "sky": "sky",
    "flatscreen": "flat",
    "lamp": "lamp",
    "beamdump": "dump",
    "dome": "dome",
    "covercalibrator": "covers",
    "tertiaryOCA": "m3",
    "tertiary": "m3",
    "mirror": "mirror",
    "splitter": "split",
    "rotator": "derot",
    "filterwheel": "fw",
    "fiber": "fiber",
    "camera": "camera",
    "spectrograph": "spec",
}
GENERIC_GLYPH = "part"

_SYMBOL = re.compile(r'<symbol id="(?P<id>[\w-]+)" viewBox="(?P<box>[^"]+)">(?:<title>(?P<title>[^<]*)</title>)?(?P<body>.*?)</symbol>', re.S)
_STROKE = re.compile(r'stroke-width="(?P<w>[\d.]+)"')


@cache
def _sprite() -> str:
    return files(__package__).joinpath("glyphs.svg").read_text(encoding="utf-8")


@cache
def _symbols() -> dict[str, tuple[str, str]]:
    return {m["id"]: (m["box"], m["body"]) for m in _SYMBOL.finditer(_sprite())}


@cache
def _titles() -> dict[str, str]:
    return {m["id"]: m["title"] or m["id"] for m in _SYMBOL.finditer(_sprite())}


def sprite() -> str:
    """The whole sprite, for renderers that ``<use>`` its symbols."""
    return _sprite()


def glyph_names() -> tuple[str, ...]:
    return tuple(_symbols())


def glyph_title(glyph: str) -> str:
    """What a symbol stands for, in words (the sprite's ``<title>``): legends, tooltips."""
    return _titles().get(glyph, glyph)


def glyph_of(graph: OpticalGraph, name: str) -> str:
    """The symbol component ``name`` wears: ``display.glyph``, else its kind's, else a generic part."""
    spec = graph.components[name]
    authored = spec.display.glyph if spec.display is not None else None
    glyph = authored or KIND_GLYPHS.get(spec.kind, GENERIC_GLYPH)
    return glyph if glyph in _symbols() else GENERIC_GLYPH


@cache
def glyph_svg(glyph: str, color: str, stroke: float = 1.6) -> bytes:
    """One symbol as a standalone SVG document in ``color``; ``stroke`` is the base stroke width in
    the 80-unit box (1.6 as drawn; heavier for small sizes), other widths scale with it."""
    box, body = _symbols().get(glyph, _symbols()[GENERIC_GLYPH])
    scale = stroke / 1.6
    body = _STROKE.sub(lambda m: f'stroke-width="{float(m["w"]) * scale:g}"', body).replace("currentColor", color)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}" fill="none" stroke="{color}" stroke-width="{stroke:g}">{body}</svg>'.encode()


@dataclass(frozen=True)
class Theme:
    name: str
    tokens: Mapping[str, Any]

    def __getattr__(self, token: str):
        try:
            return self.tokens[token]
        except KeyError:
            raise AttributeError(f"theme {self.name!r} has no token {token!r}") from None

    def light_color(self, light_class: str) -> str:
        """The colour of what travels: a light class by its family (``lamp.thar``, else ``lamp``,
        else ``light``), or the ``dark`` / ``unknown`` state."""
        if light_class == DARK:
            return self.tokens["dark"]
        if light_class == UNDEFINED:
            return self.tokens["unknown"]
        light = self.tokens["light"]
        parts = light_class.split(".")
        for n in range(len(parts), 0, -1):
            if (color := light.get(".".join(parts[:n]))) is not None:
                return color
        return light["light"]

    def stroke(self, light_class: str | None) -> Mapping[str, Any]:
        """The line grammar for a wire carrying ``light_class`` (``None``: nothing, a possible way)."""
        if light_class is None:
            return self.tokens["line"]["idle"]
        if light_class in (DARK, UNDEFINED):
            return self.tokens["line"]["dark" if light_class == DARK else "unknown"]
        return self.tokens["line"]["light"]


@cache
def _themes() -> dict[str, Mapping[str, Any]]:
    return json.loads(files(__package__).joinpath("tokens.json").read_text(encoding="utf-8"))["themes"]


def theme(name: str) -> Theme:
    """A theme of ``tokens.json``: ``toi`` (observatory white in TOI's palette), ``holo`` (owies)."""
    try:
        return Theme(name, _themes()[name])
    except KeyError:
        raise KeyError(f"no optics theme {name!r} (themes: {', '.join(_themes())})") from None
