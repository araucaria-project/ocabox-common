import unittest
import xml.etree.ElementTree as ET

from obcom.optics import parse_graph
from obcom.optics.look import GENERIC_GLYPH, KIND_GLYPHS, glyph_names, glyph_of, glyph_svg, theme
from test.optics.fixtures import BESO, jk15


class TestGlyphs(unittest.TestCase):

    def test_every_kind_glyph_is_in_the_sprite(self):
        self.assertLessEqual(set(KIND_GLYPHS.values()) | {GENERIC_GLYPH, "guider"}, set(glyph_names()))

    def test_a_component_wears_its_kind_or_the_authored_glyph(self):
        components = jk15()
        components["guider"] = {**components["guider"], "display": {"glyph": "guider"}}
        g = parse_graph(components)
        self.assertEqual(glyph_of(g, "tertiary"), "m3")
        self.assertEqual(glyph_of(g, "camera"), "camera")
        self.assertEqual(glyph_of(g, "guider"), "guider")
        self.assertEqual(glyph_of(parse_graph(BESO), "m4"), "mirror")

    def test_a_standalone_glyph_is_valid_svg_in_one_colour(self):
        svg = glyph_svg("m3", "#1C1C1C", stroke=3.2)
        root = ET.fromstring(svg)
        self.assertEqual(root.get("viewBox"), "-40 -40 80 80")
        self.assertNotIn(b"currentColor", svg)
        self.assertIn(b'stroke-width="8"', svg)  # the mirror's 4 scaled with the base 1.6 → 3.2

    def test_an_unknown_glyph_falls_back(self):
        self.assertEqual(glyph_svg("nope", "#000"), glyph_svg(GENERIC_GLYPH, "#000"))


class TestThemes(unittest.TestCase):

    def test_light_classes_resolve_by_family(self):
        toi = theme("toi")
        self.assertEqual(toi.light_color("lamp.thar"), toi.light["lamp.thar"])
        self.assertEqual(toi.light_color("lamp.neon"), toi.light["lamp"])
        self.assertEqual(toi.light_color("laser.green"), toi.light["light"])
        self.assertEqual(toi.light_color("dark"), toi.dark)
        self.assertEqual(toi.light_color("undefined"), toi.unknown)

    def test_every_theme_has_the_same_tokens(self):
        toi, holo = theme("toi"), theme("holo")
        self.assertEqual(set(toi.tokens), set(holo.tokens))
        self.assertEqual(set(toi.line), set(holo.line))
        self.assertEqual(set(toi.light), set(holo.light))

    def test_unknown_theme(self):
        with self.assertRaises(KeyError):
            theme("nostromo")


class TestSchematicSvg(unittest.TestCase):

    def test_structure_only_is_valid_svg_with_every_component(self):
        from obcom.optics.look.svg import schematic_svg
        g = parse_graph(BESO)
        root = ET.fromstring(schematic_svg(g))
        names = {e.get("data-name") for e in root.iter("{http://www.w3.org/2000/svg}g") if e.get("data-name")}
        self.assertEqual(names, set(g.nodes))

    def test_lit_by_a_state(self):
        from obcom.optics import published_values
        from obcom.optics.look.svg import schematic_svg
        g = parse_graph(BESO)
        positions = {"sky": "science", "dome": "open", "covercalibrator": "open", "covercalibrator.calibrator": "off",
                     "tertiary": "beso", "m4": "calib", "m5": "thar", "thar_lamp": "on", "white_lamp": "off"}
        svg = schematic_svg(g, "holo", published_values(positions), vertical=True)
        ET.fromstring(svg)
        self.assertIn(theme("holo").light["lamp.thar"], svg)
        self.assertIn('class="spark"', svg)


class TestGlyphTitles(unittest.TestCase):

    def test_every_glyph_says_what_it_stands_for(self):
        from obcom.optics.look import glyph_title
        self.assertEqual(glyph_title("m3"), "M3, a switch between output ports")
        self.assertEqual(glyph_title("split"), "beam splitter, pick-off")
        self.assertEqual(glyph_title("nope"), "nope")
