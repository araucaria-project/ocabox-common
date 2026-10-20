import unittest

from obcom.optics import parse_graph, place_labels, published_values, schematic
from obcom.optics.layout import INPUT_GAP, MAJOR_GAP, MINOR_GAP
from test.optics.fixtures import BESO, TMMT, jk15, night


def grid(s) -> dict[str, tuple[float, float]]:
    return {name: (p.column, p.row) for name, p in s.places.items()}


def full_jk15() -> dict:
    """jk15 as on the telescope: the camera branch and, behind M3, BESO with its pick-off and lamps."""
    return jk15(
        guider_beso={"kind": "camera", "optics": {"from": {"beso_pickoff": "guide"}}},
        beso_pickoff={"kind": "splitter", "optics": {"from": {"tertiary": "beso"}}},
        fiber={"kind": "fiber", "optics": {"from": {"beso_pickoff": "main"}}},
        **{name: BESO[name] for name in ("thar_lamp", "white_lamp", "m5", "m4", "beso")},
    )


class TestSchematic(unittest.TestCase):

    def test_the_light_goes_straight_on(self):
        places = grid(schematic(parse_graph(jk15())))
        main = ["dome", "covercalibrator", "tertiary", "derotator", "pickoff", "filterwheel", "camera"]
        self.assertEqual([places[n][0] for n in main], [float(c) for c in range(1, len(main) + 1)])
        self.assertEqual(len({places[n][1] for n in main}), 1)

    def test_a_switch_into_equal_branches_sits_between_them(self):
        places = grid(schematic(parse_graph(full_jk15())))
        camera_branch = {places[n][1] for n in ("derotator", "pickoff", "filterwheel", "camera")}
        beso_branch = {places[n][1] for n in ("beso_pickoff", "fiber", "m4", "beso")}
        self.assertEqual((len(camera_branch), len(beso_branch)), (1, 1))
        left, right = camera_branch.pop(), beso_branch.pop()
        self.assertEqual(right - left, MAJOR_GAP)  # a corridor for the labels of both
        self.assertEqual(places["tertiary"][1], (left + right) / 2)

    def test_a_short_branch_stands_beside_its_switch_further_out(self):
        places = grid(schematic(parse_graph(full_jk15())))
        self.assertEqual(places["guider"], (places["pickoff"][0], places["pickoff"][1] - MINOR_GAP))  # the camera branch is left
        self.assertEqual(places["guider_beso"], (places["beso_pickoff"][0], places["beso_pickoff"][1] + MINOR_GAP))

    def test_a_side_feeder_stands_beside_what_it_feeds_its_inputs_over_it(self):
        places = grid(schematic(parse_graph(BESO)))
        self.assertEqual(places["m5"][0], places["m4"][0])
        self.assertGreater(places["m5"][1], places["m4"][1])
        thar, white = places["thar_lamp"], places["white_lamp"]
        self.assertEqual((thar[0], white[0]), (places["m5"][0] - 1, places["m5"][0] - 1))
        self.assertAlmostEqual((thar[1] + white[1]) / 2, places["m5"][1])
        self.assertAlmostEqual(white[1] - thar[1], INPUT_GAP)

    def test_the_sky_and_the_flat_screen_side_by_side_over_the_dome(self):
        places = grid(schematic(parse_graph(jk15())))
        self.assertEqual((places["sky"][0], places["flatscreen"][0]), (0.0, 0.0))
        self.assertAlmostEqual((places["sky"][1] + places["flatscreen"][1]) / 2, places["dome"][1])

    def test_wires_are_orthogonal_out_of_a_switch_across_into_a_fan_in_from_above(self):
        s = schematic(parse_graph(full_jk15()))
        wires = {(w.upstream, w.downstream): w for w in s.wires}
        out_of_m3 = wires[("tertiary", "derotator")]
        self.assertEqual(out_of_m3.port, "andor")
        self.assertEqual(out_of_m3.points[1][0], out_of_m3.points[0][0])  # across at the switch's level first
        into_dome = wires[("sky", "dome")]
        self.assertEqual([p[0] for p in into_dome.points], [0.0, 0.5, 0.5, 1.0])  # down, across halfway, down
        self.assertEqual(wires[("m5", "m4")].input, "calib")
        for w in s.wires:
            for (x0, y0), (x1, y1) in zip(w.points, w.points[1:]):
                self.assertTrue(x0 == x1 or y0 == y1, w)

    def test_authored_places_win(self):
        components = jk15()
        components["flatscreen"] = {**components["flatscreen"], "display": {"x": 0, "y": -1}}
        place = schematic(parse_graph(components)).places["flatscreen"]
        self.assertEqual((place.column, place.row, place.authored), (0.0, -1.0, True))

    def test_detectors_side_by_side_without_switches(self):
        s = schematic(parse_graph(TMMT))
        rows = sorted(p.row for name, p in s.places.items() if name != "sky")
        self.assertEqual(rows, [0.0, MINOR_GAP, 2 * MINOR_GAP])
        self.assertEqual(s.places["sky"].row, MINOR_GAP)

    def test_light_on_every_wire(self):
        g = parse_graph(jk15())
        s = schematic(g)
        light = {(w.upstream, w.downstream): c for w, c in s.light(g, published_values({**{k: v.position for k, v in night(covercalibrator="close").selectors.items()}, "sky": "science"})).items()}
        self.assertEqual(light[("dome", "covercalibrator")], {"sky.science"})
        self.assertEqual(light[("covercalibrator", "tertiary")], {"dark"})


class TestLabels(unittest.TestCase):
    """Labels beside their components, clear of every wire, component and other label."""

    LANE, LAYER, RADIUS = 64, 76, 18

    def labels(self, components):
        s = schematic(parse_graph(components))
        center = lambda p: ((p[1] + 0.5) * self.LANE, (p[0] + 0.5) * self.LAYER)
        sizes = {name: (6.5 * len(name), 26) for name in s.places}
        bounds = (-16, -16, s.rows * self.LANE + 32, s.columns * self.LAYER + 32)  # with a renderer's margin
        return s, place_labels(s, sizes, center, self.RADIUS, bounds), center

    def test_every_label_is_clear(self):
        _, labels, _ = self.labels(full_jk15())
        self.assertEqual([n for n, l in labels.items() if not l.clear], [])

    def test_no_two_labels_meet_and_none_covers_a_component(self):
        s, labels, center = self.labels(full_jk15())
        boxes = [l.box for l in labels.values()]
        for i, (x, y, w, h) in enumerate(boxes):
            for (x2, y2, w2, h2) in boxes[i + 1:]:
                self.assertFalse(x < x2 + w2 and x2 < x + w and y < y2 + h2 and y2 < y + h)
            for p in s.places.values():
                cx, cy = center((p.column, p.row))
                self.assertFalse(x < cx + self.RADIUS and cx - self.RADIUS < x + w and y < cy + self.RADIUS and cy - self.RADIUS < y + h)

    def test_a_switch_whose_wires_leave_sideways_is_labelled_under_it(self):
        _, labels, _ = self.labels(full_jk15())
        self.assertEqual(labels["tertiary"].side, "below")
        self.assertEqual(labels["covercalibrator"].side, "right")  # beside, where nothing runs


if __name__ == "__main__":
    unittest.main()
