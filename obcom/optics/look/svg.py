"""The schematic as one SVG document — the config's preview in CI and review, and the reference
for owies. Same layout (:func:`obcom.optics.schematic`), glyphs and tokens as TOI's QPainter
drawing; light flows as a CSS animation, which a still viewer simply does not play.

Without a state it draws the structure only: every wire a possible way, every component idle.
"""

from __future__ import annotations

from html import escape

from datamodels.optics import DARK, UNDEFINED, Archetype

from obcom.optics.graph import OpticalGraph
from obcom.optics.labels import component_label
from obcom.optics.layout import place_labels, schematic
from obcom.optics.look import Theme, glyph_of, sprite, theme
from obcom.optics.sees import ValuesOf

LANE, LAYER = 64, 76  #: px between lanes (across the light) and layers (along it)
RADIUS, MARGIN, CORNER = 18, 16, 10
NAME_PX, STATE_PX, CHAR = 11, 10, 0.58  #: font sizes; a character's width in ems, for measuring without a font


def schematic_svg(graph: OpticalGraph, look: str | Theme = "toi", values_of: ValuesOf | None = None,
                  vertical: bool = False, title: str | None = None) -> str:
    """The schematic of ``graph`` in theme ``look``; ``values_of`` lights it (else structure only);
    ``vertical`` runs the light down the page instead of across."""
    t = theme(look) if isinstance(look, str) else look
    layout = schematic(graph)
    columns, rows = layout.columns, layout.rows
    across, down = (rows * LANE, columns * LAYER) if vertical else (columns * LAYER, rows * LANE)
    width, height = across + 2 * MARGIN, down + 2 * MARGIN

    def center(column: float, row: float) -> tuple[float, float]:
        if vertical:
            return MARGIN + (row + 0.5) * LANE, MARGIN + (column + 0.5) * LAYER
        return MARGIN + (column + 0.5) * LAYER, MARGIN + (row + 0.5) * LANE

    light_on = layout.light(graph, values_of) if values_of is not None else {}
    lit_from: dict[str, str] = {}
    wires = []
    for wire in layout.wires:
        light = None
        if values_of is not None and wire.taken(graph, values_of):
            light = _dominant(light_on[wire])
            if light not in (DARK, UNDEFINED):
                lit_from[wire.upstream] = light
        wires.append(_wire(t, _rounded([center(*p) for p in wire.points]), light))

    texts: dict[str, tuple[str, str, str]] = {}  #: name, state, state colour
    nodes = []
    for name, place in layout.places.items():
        x, y = center(place.column, place.row)
        node = graph.node(name)
        ring, ring_width, dashed = t.idle, t.ring["width"], False
        state = ""
        if values_of is not None:
            values = values_of(node)
            if any(v is None for v in values.values()):
                ring, ring_width, dashed, state = t.unknown, t.ring["accent_width"], True, "unknown"
            elif name in lit_from:
                ring = t.light_color(lit_from[name])
            if node.archetype == Archetype.SELECTOR and values.get(None):
                state = state or values[None]
        dash = f' stroke-dasharray="{" ".join(map(str, t.ring["dash"]))}"' if dashed else ""
        texts[name] = (component_label(graph, name), state, t.unknown if dashed else t.muted)
        nodes.append(
            f'<g class="component" data-name="{escape(name)}">'
            f'<circle cx="{x:g}" cy="{y:g}" r="{RADIUS}" fill="{t.ground}" stroke="{ring}" stroke-width="{ring_width}"{dash}/>'
            f'<use href="#{glyph_of(graph, name)}" x="{x - RADIUS * 1.1:g}" y="{y - RADIUS * 1.1:g}" width="{RADIUS * 2.2:g}" height="{RADIUS * 2.2:g}" style="color:{t.unknown if dashed else t.glyph}"/>'
        )

    sizes = {name: (CHAR * max(NAME_PX * len(label), STATE_PX * len(state)), NAME_PX + 3 + (STATE_PX + 3 if state else 0))
             for name, (label, state, _) in texts.items()}
    labels = place_labels(layout, sizes, lambda p: center(*p), RADIUS, (0, 0, width, height))
    for i, name in enumerate(layout.places):
        label, state, state_color = texts[name]
        box = labels[name]
        anchor, x = {"right": ("start", box.x), "left": ("end", box.x + box.width)}.get(box.side, ("middle", box.x + box.width / 2))
        weight = ' font-weight="700"' if graph.node(name).archetype == Archetype.DETECTOR else ""
        nodes[i] += (
            f'<text x="{x:g}" y="{box.y + NAME_PX:g}" text-anchor="{anchor}" font-size="{NAME_PX}" fill="{t.text}"{weight}>{escape(label)}</text>'
            + (f'<text x="{x:g}" y="{box.y + NAME_PX + 3 + STATE_PX:g}" text-anchor="{anchor}" font-size="{STATE_PX}" fill="{state_color}">{escape(state)}</text>' if state else "")
            + "</g>"
        )

    symbols = sprite().split(">", 1)[1].rsplit("</svg>", 1)[0]  # the sprite's symbols, without its root
    spark = t.line["light"]
    name = escape(title or graph.telescope or "optical path")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {height:g}" width="{width:g}" height="{height:g}" '
        f'role="img" aria-label="{name}" font-family="DejaVu Sans, Segoe UI, sans-serif">'
        f"<title>{name}</title>"
        f"<style>@keyframes flow {{ to {{ stroke-dashoffset: -{sum(spark['sparks'])}; }} }} "
        f".spark {{ animation: flow {spark['period_s']}s linear infinite; }}</style>"
        f"<defs>{symbols}</defs>"
        f'<rect width="100%" height="100%" fill="{t.ground}"/>'
        + "".join(wires) + "".join(nodes) + "</svg>"
    )


def _dominant(classes) -> str:
    light = sorted(c for c in classes if c not in (DARK, UNDEFINED))
    if light:
        return light[0]
    return UNDEFINED if UNDEFINED in classes else DARK


def _rounded(points: list[tuple[float, float]]) -> str:
    """An orthogonal polyline with rounded corners, as path data."""
    d = [f"M{points[0][0]:g} {points[0][1]:g}"]
    for (x0, y0), (x1, y1), (x2, y2) in zip(points, points[1:], points[2:]):
        r = min(CORNER, (abs(x1 - x0) + abs(y1 - y0)) / 2, (abs(x2 - x1) + abs(y2 - y1)) / 2)
        ax, ay = x1 - _sign(x1 - x0) * r, y1 - _sign(y1 - y0) * r
        bx, by = x1 + _sign(x2 - x1) * r, y1 + _sign(y2 - y1) * r
        d.append(f"L{ax:g} {ay:g} Q{x1:g} {y1:g} {bx:g} {by:g}")
    d.append(f"L{points[-1][0]:g} {points[-1][1]:g}")
    return " ".join(d)


def _sign(v: float) -> int:
    return (v > 0) - (v < 0)


def _wire(t: Theme, d: str, light: str | None) -> str:
    stroke = t.stroke(light)
    color = t.idle if light is None else t.light_color(light)
    common = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    if light is not None and light not in (DARK, UNDEFINED):
        return (f'<path d="{d}" {common} stroke="{color}" stroke-width="{stroke["halo"]}" stroke-opacity="{stroke["halo_opacity"]}"/>'
                f'<path d="{d}" {common} stroke="{color}" stroke-width="{stroke["width"]}"/>'
                f'<path class="spark" d="{d}" {common} stroke="{stroke["spark"]}" stroke-width="{stroke["width"]}" '
                f'stroke-dasharray="{" ".join(map(str, stroke["sparks"]))}"/>')
    dash = f' stroke-dasharray="{" ".join(map(str, stroke["dash"]))}"' if "dash" in stroke else ""
    return f'<path d="{d}" {common} stroke="{color}" stroke-width="{stroke["width"]}"{dash}/>'
