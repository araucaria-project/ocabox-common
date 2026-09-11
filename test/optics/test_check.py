import unittest

from datamodels.optics import Active, Collision, Impossible, Settable

from obcom.optics import Hold, ProvenState, UnknownFunction, check, check_result, parse_graph, resolve
from test.optics.fixtures import BESO, TMMT, jk15, night


class TestCheckJk15(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())

    def test_active_reports_proven_positions_on_the_path_only(self):
        verdict = check(self.g, night(), "camera", "object")
        self.assertIsInstance(verdict, Active)
        self.assertEqual(verdict.see, "sky.science")
        self.assertEqual(verdict.positions, {"dome": "open", "covercalibrator": "open", "covercalibrator.calibrator": "off", "tertiary": "andor"})

    def test_unreported_aspect_is_undefined_never_assumed_off(self):
        state = ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "dome": "open"}, sun_alt_deg=-30)
        verdict = check(self.g, state, "camera", "object")
        self.assertIsInstance(verdict, Settable)  # commanding the lamp off is the move that proves it
        self.assertEqual(verdict.moves, {"covercalibrator.calibrator": "off"})

    def test_settable_lists_only_the_moves(self):
        verdict = check(self.g, night(), "camera", "dark")
        self.assertIsInstance(verdict, Settable)
        self.assertEqual(verdict.moves, {"covercalibrator": "close"})
        self.assertEqual(verdict.positions, {"covercalibrator": "close", "covercalibrator.calibrator": "off", "tertiary": "andor"})

    def test_unmapped_m3_is_settable_by_moving_it(self):
        state = night(tertiary=None)
        verdict = check(self.g, state, "camera", "object")
        self.assertIsInstance(verdict, Settable)
        self.assertEqual(verdict.moves, {"tertiary": "andor"})

    def test_contaminated_view_is_not_active(self):
        state = night(**{"covercalibrator.calibrator": "on"})
        verdict = check(self.g, state, "camera", "object")
        self.assertIsInstance(verdict, Settable)
        self.assertEqual(verdict.moves, {"covercalibrator.calibrator": "off"})
        zero = check(self.g, state, "camera", "zero")
        self.assertIsInstance(zero, Settable)
        self.assertEqual(zero.moves, {"tertiary": "beso"})  # the only single move that yields a clean dark

    def test_idle_branch_dark_is_active(self):
        self.assertIsInstance(check(self.g, night(tertiary="beso"), "camera", "dark"), Active)

    def test_dark_strict_pins_the_terminal_with_via(self):
        at_night = check(self.g, night(tertiary="beso"), "camera", "dark_strict")
        self.assertIsInstance(at_night, Settable)
        self.assertEqual(at_night.moves, {"covercalibrator": "close", "tertiary": "andor"})  # first alternative applies at night
        closed = check(self.g, night(covercalibrator="close"), "camera", "dark_strict")
        self.assertIsInstance(closed, Active)
        by_day = check(self.g, night(sun_alt_deg=20, tertiary="beso"), "camera", "dark_strict")
        self.assertIsInstance(by_day, Active)  # `when: sky.science` fails by day → second alternative (via tertiary=beso) is active

    def test_impossible_names_the_unavailable_class(self):
        verdict = check(self.g, night(sun_alt_deg=20), "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertEqual(verdict.unavailable, "sky.science")
        self.assertIn("sky.day", verdict.reason)

    def test_impossible_lists_undefined_terminals(self):
        verdict = check(self.g, ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open"}), "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertEqual(verdict.undefined_at, ("sky",))
        unreported_lamp = check(self.g, ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "dome": "open"}), "camera", "object")
        self.assertEqual(unreported_lamp.undefined_at, ("covercalibrator", "covercalibrator.calibrator", "sky"))

    def test_domeflat_by_day_moves_the_dome_to_the_screen(self):
        verdict = check(self.g, night(sun_alt_deg=20), "camera", "domeflat")
        self.assertIsInstance(verdict, Settable)
        self.assertEqual(verdict.moves, {"dome": "flat"})

    def test_collision_names_the_holder(self):
        state = ProvenState(night(tertiary="beso").selectors, night().environment, holds={"tertiary": Hold("beso", "beso-run")})
        verdict = check(self.g, state, "camera", "object")
        self.assertIsInstance(verdict, Collision)
        self.assertEqual((verdict.selector, verdict.required, verdict.held, verdict.holder), ("tertiary", "andor", "beso", "beso-run"))
        self.assertIn("beso-run", verdict.reason)

    def test_hold_consistent_with_the_goal_is_no_collision(self):
        state = ProvenState(night().selectors, night().environment, holds={"tertiary": Hold("andor", "imaging")})
        self.assertIsInstance(check(self.g, state, "camera", "object"), Active)
        self.assertIsInstance(check(self.g, state, "camera", "dark"), Settable)  # dark via cover does not touch the held M3

    def test_collision_falls_back_to_another_route_when_one_exists(self):
        state = ProvenState(night().selectors, night().environment, holds={"covercalibrator": Hold("open", "someone")})
        verdict = check(self.g, state, "camera", "dark")
        self.assertIsInstance(verdict, Settable)
        self.assertNotIn("covercalibrator", verdict.moves)

    def test_unknown_function_is_a_caller_error(self):
        with self.assertRaises(UnknownFunction):
            check(self.g, night(), "camera", "spectroscopy")
        with self.assertRaises(UnknownFunction):
            check(self.g, night(), "guider", "object")  # guider declares no paths

    def test_check_result_is_addressed(self):
        result = check_result(self.g, night(), "camera", "object")
        self.assertEqual((result.detector, result.function, result.verdict.kind.value), ("camera", "object", "active"))

    def test_resolve(self):
        self.assertEqual(resolve(self.g, night(), "camera", "object"), {"dome": "open", "covercalibrator": "open", "covercalibrator.calibrator": "off", "tertiary": "andor"})
        self.assertEqual(resolve(self.g, night(), "camera", "dark"), {"covercalibrator": "close", "covercalibrator.calibrator": "off", "tertiary": "andor"})
        self.assertIsNone(resolve(self.g, night(sun_alt_deg=20), "camera", "object"))


class TestPreferenceOrder(unittest.TestCase):

    def test_fewest_moves_first(self):
        g = parse_graph(jk15())
        verdict = check(g, night(tertiary=None), "camera", "dark")
        self.assertEqual(verdict.moves, {"tertiary": "beso"})  # cover-close would also need M3 proven at andor: two moves

    def test_least_collateral_then_closest_terminal(self):
        g = parse_graph(jk15())
        verdict = check(g, night(), "camera", "dark")
        # dome closed / cover closed / M3 away all cost one move; M3 away also changes what guider_beso sees,
        # dome and cover tie on collateral and the cover is the closer terminal
        self.assertEqual(verdict.moves, {"covercalibrator": "close"})


class TestCheckBesoAndTmmt(unittest.TestCase):

    def test_fan_in_routes(self):
        g = parse_graph(BESO)
        state = ProvenState.build({"tertiary": "beso", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open", "m4": "sky", "m5": "thar", "thar_lamp": "on", "white_lamp": "on"}, sun_alt_deg=-30)
        self.assertIsInstance(check(g, state, "beso", "object"), Active)
        arc = check(g, state, "beso", "arc")
        self.assertEqual(arc.moves, {"m4": "calib"})
        flat = check(g, state, "beso", "flat")
        self.assertEqual(flat.moves, {"m4": "calib", "m5": "white"})

    def test_lamp_proven_off_makes_the_arc_impossible(self):
        g = parse_graph(BESO)
        state = ProvenState.build({"tertiary": "beso", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open", "m4": "calib", "m5": "thar", "thar_lamp": "off"}, sun_alt_deg=-30)
        verdict = check(g, state, "beso", "arc")
        self.assertIsInstance(verdict, Impossible)
        self.assertIn("thar_lamp", verdict.reason)

    def test_unusable_lamp_telemetry_never_makes_a_route_settable(self):
        g = parse_graph(BESO)
        base = {"tertiary": "beso", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open", "m4": "sky", "m5": "thar"}
        state = ProvenState.build(base, stale=["thar_lamp"], sun_alt_deg=-30)
        verdict = check(g, state, "beso", "arc")
        self.assertIsInstance(verdict, Impossible)
        self.assertIn("thar_lamp", verdict.undefined_at)

    def test_no_selectors_at_all(self):
        g = parse_graph(TMMT)
        self.assertIsInstance(check(g, ProvenState.build({}, sun_alt_deg=-30), "cam_a", "object"), Active)
        self.assertIsInstance(check(g, ProvenState.build({}, sun_alt_deg=-30), "cam_a", "skyflat"), Impossible)
        self.assertEqual(check(g, ProvenState.build({}, sun_alt_deg=-30), "cam_a", "object").positions, {})


if __name__ == "__main__":
    unittest.main()
