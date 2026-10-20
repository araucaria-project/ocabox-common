import unittest

from obcom.optics import parse_graph, published_values, schematic
from test.optics.fixtures import BESO, TMMT, jk15, night


def grid(s) -> dict[str, tuple[float, float]]:
    return {name: (p.column, p.row) for name, p in s.places.items()}


class TestSchematic(unittest.TestCase):

    def test_the_main_line_runs_straight_and_branches_drop_below(self):
        places = grid(schematic(parse_graph(jk15())))
        main = ["sky", "dome", "covercalibrator", "tertiary", "derotator", "pickoff", "filterwheel", "camera"]
        self.assertEqual([places[n] for n in main], [(float(c), 0.0) for c in range(len(main))])
        self.assertEqual(places["guider"], (6.0, 1.0))  # out of the pick-off, a row below
        self.assertEqual(places["flatscreen"], (0.0, 1.0))  # beside the dome it feeds, under the sky

    def test_lamps_sit_beside_the_mirror_they_feed(self):
        places = grid(schematic(parse_graph(BESO)))
        self.assertEqual(places["beso"][1], 0.0)  # the deepest way from M3 goes straight on
        self.assertEqual(places["m5"], (places["m4"][0] - 1, 2.0))
        self.assertEqual(places["thar_lamp"], (places["m5"][0] - 1, places["m5"][1]))  # first input straight on
        self.assertEqual(places["white_lamp"], (places["m5"][0] - 1, places["m5"][1] + 1))

    def test_wires_are_orthogonal_leaving_switches_and_entering_fan_ins_vertically(self):
        s = schematic(parse_graph(BESO))
        wires = {(w.upstream, w.downstream): w for w in s.wires}
        out_of_m3 = wires[("tertiary", "camera")]
        self.assertEqual(out_of_m3.port, "andor")
        self.assertEqual(out_of_m3.points[1][0], out_of_m3.points[0][0])  # down first, out of the switch
        into_m4 = wires[("m5", "m4")]
        self.assertEqual(into_m4.input, "calib")
        self.assertEqual(into_m4.points[1][0], into_m4.points[2][0])  # across first, then up into the fan-in
        for w in s.wires:
            for (x0, y0), (x1, y1) in zip(w.points, w.points[1:]):
                self.assertTrue(x0 == x1 or y0 == y1, w)

    def test_authored_places_win(self):
        components = jk15()
        components["flatscreen"] = {**components["flatscreen"], "display": {"x": 0, "y": -1}}
        place = schematic(parse_graph(components)).places["flatscreen"]
        self.assertEqual((place.column, place.row, place.authored), (0.0, -1.0, True))

    def test_without_switches(self):
        s = schematic(parse_graph(TMMT))
        self.assertEqual((s.columns, s.rows), (2, 3))

    def test_light_on_every_wire(self):
        g = parse_graph(jk15())
        s = schematic(g)
        light = {(w.upstream, w.downstream): c for w, c in s.light(g, published_values({**{k: v.position for k, v in night(covercalibrator="close").selectors.items()}, "sky": "science"})).items()}
        self.assertEqual(light[("dome", "covercalibrator")], {"sky.science"})
        self.assertEqual(light[("covercalibrator", "tertiary")], {"dark"})
