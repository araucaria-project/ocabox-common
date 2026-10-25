import unittest

from obcom.optics import default_detector, is_selected, parse_graph, positions_of, routing_selectors, selected_by, station
from test.optics.fixtures import BESO, jk15

#: wk06's shape: no M3 — covers, a pick-off for the guider, the camera behind the filter wheel
WK06 = {
    "sky": {"kind": "sky"},
    "covercalibrator": {"kind": "covercalibrator", "optics": {"from": "sky"}},
    "pickoff": {"kind": "splitter", "optics": {"from": "covercalibrator"}},
    "filterwheel": {"kind": "filterwheel", "optics": {"from": {"pickoff": "main"}}},
    "guider": {"kind": "camera", "optics": {"from": {"pickoff": "guide"}}},
    "camera": {"kind": "camera", "optics": {"from": "filterwheel"}, "paths": {"object": "sky.science", "dark": "dark"}},
}


class TestStations(unittest.TestCase):
    """Which detectors the telescope is set up for: M3 routes, the covers and the dome do not."""

    def setUp(self):
        self.g = parse_graph(jk15())

    def test_only_m3_routes_between_detectors(self):
        self.assertEqual(routing_selectors(self.g), ("tertiary",))
        self.assertEqual(selected_by(self.g, "camera"), {"tertiary": frozenset({"andor"})})
        self.assertEqual(selected_by(self.g, "guider_beso"), {"tertiary": frozenset({"beso"})})

    def test_a_station_is_an_instrument_and_its_guider(self):
        self.assertEqual(station(self.g, "camera"), ("guider", "camera"))  # configuration order
        self.assertEqual(station(self.g, "guider"), ("guider", "camera"))

    def test_the_default_follows_m3_not_the_light(self):
        self.assertEqual(default_detector(self.g, {"tertiary": "andor", "covercalibrator": "close"}), "camera")  # covers shut: still the camera's
        self.assertIsNone(default_detector(self.g, {"tertiary": None}))  # not known: decide nothing
        self.assertIs(is_selected(self.g, "guider", {"tertiary": "beso"}), False)
        self.assertEqual(default_detector(parse_graph(BESO), {"tertiary": "beso", "m4": "park"}), "beso")  # M4 chooses what, not to whom

    def test_the_instrument_comes_before_its_guider(self):
        self.assertEqual(default_detector(parse_graph(WK06), {}), "camera")  # no routing selector: all selected

    def test_without_a_routing_selector_every_detector_is_selected(self):
        g = parse_graph(WK06)
        self.assertEqual(routing_selectors(g), ())
        self.assertEqual(station(g, "camera"), ("guider", "camera"))

    def test_positions_from_what_the_service_publishes(self):
        published = {"tertiary": {"position": "beso", "origin": "read"}, "dome": {"position": None, "cause": "shutter state unknown"}}
        self.assertEqual(positions_of(published), {"tertiary": "beso", "dome": None})
        self.assertEqual(default_detector(parse_graph(BESO), positions_of(published)), "beso")


if __name__ == "__main__":
    unittest.main()
