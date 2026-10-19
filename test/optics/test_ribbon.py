import unittest

from obcom.optics import ProvenState, StopRole, parse_graph, published_values, ribbon
from test.optics.fixtures import BESO, NIGHT, jk15, night


def stops(r) -> list[tuple[str, StopRole]]:
    return [(s.name, s.role) for s in r.stops]


def beso_state(**positions) -> ProvenState:
    base = {**NIGHT, "tertiary": "beso", "m4": "sky", "m5": "thar", "thar_lamp": "on", "white_lamp": "off"}
    return ProvenState.build({**base, **positions}, sun_alt_deg=-30, dome_shutter_open=True)


class TestRibbon(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())
        self.beso = parse_graph(BESO)

    def test_starts_at_the_sky_when_the_detector_sees_it(self):
        r = ribbon(self.g, night(), "camera")
        self.assertEqual([s.name for s in r.stops], ["sky", "dome", "covercalibrator", "tertiary", "derotator", "pickoff", "filterwheel", "camera"])
        self.assertEqual(r.terminal.role, StopRole.EMITTER)
        self.assertTrue(all(s.light == {"sky.science"} for s in r.stops))

    def test_starts_at_the_lamp_when_switched_to_it(self):
        r = ribbon(self.beso, beso_state(m4="calib"), "beso")
        self.assertEqual(stops(r), [("thar_lamp", StopRole.EMITTER), ("m5", StopRole.PASS), ("m4", StopRole.PASS), ("beso", StopRole.DETECTOR)])

    def test_goes_on_past_a_blocker_to_the_light_that_does_not_arrive(self):
        r = ribbon(self.g, night(covercalibrator="close"), "camera")
        self.assertEqual(stops(r)[:3], [("sky", StopRole.UPSTREAM), ("dome", StopRole.UPSTREAM), ("covercalibrator", StopRole.BLOCKER)])
        self.assertEqual(r.stops[1].light, {"sky.science"})  # the sky reaches the closed covers
        self.assertEqual(r.stops[2].light, {"dark"})
        self.assertFalse(r.stops[0].arrives)

    def test_beyond_a_switch_proven_elsewhere_it_follows_the_proven_input(self):
        r = ribbon(self.beso, beso_state(tertiary="andor"), "beso")
        self.assertEqual(r.terminal.name, "tertiary")
        self.assertEqual(r.stops[0].name, "sky")  # m4 at sky: the way back is the fibre, not the lamps

    def test_beyond_an_unknown_switch_the_target_chooses(self):
        state = beso_state(m4=None)
        self.assertEqual(ribbon(self.beso, state, "beso").stops[0].name, "sky")  # authored order: sky first
        r = ribbon(self.beso, state, "beso", target={"m4": "calib"})
        self.assertEqual(stops(r), [("thar_lamp", StopRole.UPSTREAM), ("m5", StopRole.UPSTREAM), ("m4", StopRole.UNKNOWN), ("beso", StopRole.DETECTOR)])

    def test_the_target_never_lights_anything(self):
        r = ribbon(self.g, night(covercalibrator="close"), "camera", target={"covercalibrator": "open"})
        self.assertEqual(r.terminal.name, "covercalibrator")
        self.assertEqual(r.stops[-1].light, {"dark"})

    def test_from_published_positions_as_from_the_proven_state(self):
        state = night(covercalibrator="close")
        positions = {**{k: v.position for k, v in state.selectors.items()}, "sky": "science"}
        self.assertEqual(ribbon(self.g, published_values(positions), "camera"), ribbon(self.g, state, "camera"))

    def test_only_detectors_have_ribbons(self):
        with self.assertRaises(ValueError):
            ribbon(self.g, night(), "tertiary")


class TestSeenPositions(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())

    def seen(self, state):
        from obcom.optics import seen_positions, sees
        return seen_positions(self.g, {d: sees(self.g, state, d) for d in self.g.detectors})

    def test_the_sky_and_the_dome_read_back_from_what_the_camera_sees(self):
        found = self.seen(night(sun_alt_deg=-8.0))
        self.assertEqual(found["sky"], "flat")
        self.assertEqual(found["dome"], "open")  # the light came through it from the sky
        self.assertEqual(found["tertiary"], "andor")

    def test_a_blocker_reads_back_what_it_must_be(self):
        found = self.seen(night(covercalibrator="close"))
        self.assertEqual(found["covercalibrator"], "close")
        self.assertNotIn("sky", found)  # nothing seen of it

    def test_without_the_sky_and_dome_axes_the_drawing_agrees_with_sees(self):
        from obcom.optics import sees
        state = night(sun_alt_deg=-8.0)
        published = {k: v.position for k, v in state.selectors.items() if k != "dome"}  # an old service: no sky, no dome
        positions = {**self.seen(state), **published}
        r = ribbon(self.g, published_values(positions), "camera")
        self.assertEqual(r.stops[-1].light, {rec.light_class for rec in sees(self.g, state, "camera")})
        self.assertTrue(all(stop.arrives for stop in r.stops))
