import unittest

from datamodels.optics import UNDEFINED

from obcom.optics import parse_graph, sees
from obcom.optics.telemetry import Readings, proven_state, tertiary_symbol
from test.optics.fixtures import BESO, jk15


def classes(records):
    return {(r.light_class, r.terminal) for r in records}


class TestAlpacaToProvenState(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())

    def test_a_night_of_imaging(self):
        readings = Readings(tertiary_port=2, cover_status=3, calibrator_status=1, shutter_state=0,
                            dome_az_deg=120.0, mount_az_deg=120.0, mount_alt_deg=60.0, sun_alt_deg=-30.0)
        self.assertEqual(classes(sees(self.g, proven_state(self.g, readings), "camera")), {("sky.science", "sky")})

    def test_unmapped_port_and_moving_devices_are_undefined(self):
        self.assertIsNone(tertiary_symbol(self.g, 7))
        self.assertEqual(tertiary_symbol(self.g, 1), "beso")
        state = proven_state(self.g, Readings(tertiary_port=7, cover_status=2, calibrator_status=2, shutter_state=2, sun_alt_deg=-30.0))
        self.assertIsNone(state.selectors["tertiary"].position)
        self.assertTrue(state.selectors["covercalibrator"].moving)
        self.assertTrue(state.selectors["covercalibrator.calibrator"].moving)
        self.assertIsNone(state.environment.dome_shutter_open)
        self.assertEqual({t for _, t in classes(sees(self.g, state, "camera"))}, {"tertiary"})

    def test_no_calibrator_present_is_an_off_lamp(self):
        state = proven_state(self.g, Readings(tertiary_port=2, cover_status=3, calibrator_status=0, shutter_state=0,
                                              dome_az_deg=120.0, mount_az_deg=120.0, mount_alt_deg=60.0, sun_alt_deg=-30.0))
        self.assertEqual(state.selectors["covercalibrator.calibrator"].position, "off")

    def test_unread_devices_contribute_nothing(self):
        state = proven_state(self.g, Readings())
        self.assertEqual(state.selectors, {})
        self.assertEqual({c for c, _ in classes(sees(self.g, state, "camera"))}, {UNDEFINED})

    def test_lamps_positions_and_overrides(self):
        g = parse_graph(BESO)
        readings = Readings(tertiary_port=1, cover_status=3, calibrator_status=1, shutter_state=0, sun_alt_deg=-30.0,
                            lamps={"thar_lamp": True, "white_lamp": None}, positions={"m4": "calib", "m5": "thar", "dome": "open"})
        self.assertEqual(classes(sees(g, proven_state(g, readings), "beso")), {("lamp.thar", "thar_lamp")})
        what_if = proven_state(g, readings, overrides={"m5": "white"})
        self.assertEqual(classes(sees(g, what_if, "beso")), {(UNDEFINED, "white_lamp")})


if __name__ == "__main__":
    unittest.main()
