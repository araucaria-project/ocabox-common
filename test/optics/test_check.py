import copy
import unittest

from datamodels.optics import Active, Collision, Impossible, Settable

from obcom.optics import DEFAULT_REGISTRY, Aspect, GraphInvalid, Hold, ProvenState, Selector, UnknownFunction, check, check_result, parse_graph, resolve
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

    def test_two_terminals_of_the_goal_class_are_contamination_not_active(self):
        reg = DEFAULT_REGISTRY.copy()
        reg.register(Selector(name="tertiaryOCA", aspects=(Aspect(name="calibrator", emits="lamp"),)), "tertiary")
        camera = {**jk15()["camera"], "paths": {**jk15()["camera"]["paths"], "lampflat": "lamp"}}
        g = parse_graph(jk15(camera=camera), registry=reg)
        both = night(covercalibrator="close", **{"covercalibrator.calibrator": "on", "tertiary.calibrator": "on"})
        verdict = check(g, both, "camera", "lampflat")
        self.assertIsInstance(verdict, Settable)  # lamp @ covercalibrator and lamp @ tertiary at once: same class, still two things
        self.assertEqual(len(verdict.moves), 1)  # one move separates them (M3 away, or its lamp off)
        self.assertIsInstance(check(g, both.with_positions({"tertiary.calibrator": "off"}), "camera", "lampflat"), Active)

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

    def test_alternatives_are_ordered_fallbacks_when_the_preferred_one_is_held(self):
        idle = night(tertiary="beso")
        held = ProvenState(selectors=idle.selectors, environment=idle.environment, holds={"covercalibrator": Hold("open", "beso-run")})
        verdict = check(self.g, held, "camera", "dark_strict")  # the cover-closed alternative applies at night but collides
        self.assertIsInstance(verdict, Active)  # so the unconditional M3-away alternative answers
        self.assertEqual(verdict.positions, {"tertiary": "beso"})
        only_cover = {**jk15()["camera"], "paths": {**jk15()["camera"]["paths"], "dark_cover": {"see": "dark", "via": {"covercalibrator": "close"}}}}
        verdict = check(parse_graph(jk15(camera=only_cover)), held, "camera", "dark_cover")
        self.assertIsInstance(verdict, Collision)  # a single alternative has no fallback: the hold is the answer
        self.assertEqual(verdict.holder, "beso-run")

    def test_impossible_names_the_unavailable_class(self):
        verdict = check(self.g, night(sun_alt_deg=20), "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertEqual(verdict.unavailable, "sky.science")
        self.assertIn("sky.day", verdict.reason)

    def test_impossible_lists_the_unknown_axes(self):
        verdict = check(self.g, ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open"}), "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertEqual(verdict.undefined_at, ("sky",))
        unreported_lamp = check(self.g, ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "dome": "open"}), "camera", "object")
        # the lamp is unknown, not the covers it is part of (they read open)
        self.assertEqual(unreported_lamp.undefined_at, ("covercalibrator.calibrator", "sky"))
        self.assertNotIn("mirror covers", unreported_lamp.reason)

    def test_domeflat_by_day_moves_the_dome_to_the_screen(self):
        verdict = check(self.g, night(sun_alt_deg=20, flatscreen="on"), "camera", "domeflat")
        self.assertIsInstance(verdict, Settable)
        self.assertEqual(verdict.moves, {"dome": "flat"})

    def test_domeflat_needs_the_flat_screen_lit(self):
        unknown = check(self.g, night(sun_alt_deg=20), "camera", "domeflat")
        self.assertIsInstance(unknown, Impossible)
        self.assertIn("flat screen is not known", unknown.reason)
        off = check(self.g, night(sun_alt_deg=20, flatscreen="off"), "camera", "domeflat")
        self.assertIn("flat screen is off", off.reason)

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

    def test_selector_held_at_an_unknown_position_is_routed_around(self):
        # broken dome shutter: the dome is undefined and must not move; the cover is held open —
        # the only dark left is M3 away from the camera
        holds = {"dome": Hold(None, "the operator"), "covercalibrator": Hold("open", "the operator")}
        state = ProvenState(night(dome=None).selectors, night().environment, holds=holds)
        verdict = check(self.g, state, "camera", "dark")
        self.assertIsInstance(verdict, Settable)
        self.assertEqual(verdict.moves, {"tertiary": "beso"})

    def test_selector_held_at_an_unknown_position_never_moves(self):
        state = ProvenState(night(dome=None).selectors, night().environment, holds={"dome": Hold(None, "the operator")})
        verdict = check(self.g, state, "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertIn("dome: position unknown (its sensor reports no known position), cannot be moved — held by the operator", verdict.reason)
        self.assertIn("dome", verdict.undefined_at)

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

    def test_goals_match_light_by_family(self):
        g = parse_graph(BESO)
        state = ProvenState.build({"tertiary": "beso", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open", "m4": "calib", "m5": "white", "thar_lamp": "on", "white_lamp": "on"}, sun_alt_deg=-30)
        arc = check(g, state, "beso", "arc")  # arc wants lamp.thar; the white lamp does not do
        self.assertIsInstance(arc, Settable)
        self.assertEqual(arc.moves, {"m5": "thar"})
        any_lamp = {**BESO, "beso": {**BESO["beso"], "paths": {**BESO["beso"]["paths"], "lampflat": "lamp"}}}
        self.assertIsInstance(check(parse_graph(any_lamp), state, "beso", "lampflat"), Active)  # `lamp` accepts lamp.white

    def test_lamp_proven_off_makes_the_arc_impossible(self):
        g = parse_graph(BESO)
        state = ProvenState.build({"tertiary": "beso", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open", "m4": "calib", "m5": "thar", "thar_lamp": "off"}, sun_alt_deg=-30)
        verdict = check(g, state, "beso", "arc")
        self.assertIsInstance(verdict, Impossible)
        self.assertIn("lamp.thar is not available now: thar lamp is off", verdict.reason)

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


def _strict(**goal) -> dict:
    """jk15 whose camera ``dark_strict`` is ``goal``."""
    spec = copy.deepcopy(jk15())
    spec["camera"]["paths"]["dark_strict"] = goal
    return spec


class TestAlso(unittest.TestCase):
    """``also``: positions held although the light does not pass them — M3 turns the camera away and
    the covers close too, because stray light finds its way around a mirror."""

    def setUp(self):
        self.g = parse_graph(_strict(see="dark", via={"tertiary": "beso"}, also={"covercalibrator": "close"}))

    def test_both_are_moved(self):
        verdict = check(self.g, night(), "camera", "dark_strict")
        self.assertIsInstance(verdict, Settable)
        self.assertEqual(verdict.moves, {"tertiary": "beso", "covercalibrator": "close"})

    def test_dark_alone_is_not_enough(self):
        verdict = check(self.g, night(tertiary="beso"), "camera", "dark_strict")
        self.assertIsInstance(verdict, Settable)  # the camera sees dark already; the covers are still open
        self.assertEqual(verdict.moves, {"covercalibrator": "close"})

    def test_active_with_everything_held_and_says_so(self):
        verdict = check(self.g, night(tertiary="beso", covercalibrator="close"), "camera", "dark_strict")
        self.assertIsInstance(verdict, Active)
        self.assertEqual(verdict.positions["covercalibrator"], "close")

    def test_a_held_also_collides(self):
        state = night(tertiary="beso")
        state = ProvenState(selectors=state.selectors, environment=state.environment, holds={"covercalibrator": Hold("open", "camera exposing")})
        verdict = check(self.g, state, "camera", "dark_strict")
        self.assertIsInstance(verdict, Collision)
        self.assertEqual(verdict.selector, "covercalibrator")

    def test_config_errors(self):
        for also, code in (({"nosuch": "close"}, "also_not_a_selector"), ({"covercalibrator": "ajar"}, "undeclared_position"),
                           ({"camera": "on"}, "also_not_a_selector")):
            with self.subTest(also=also), self.assertRaises(GraphInvalid) as raised:
                parse_graph(_strict(see="dark", via={"tertiary": "beso"}, also=also))
            self.assertIn(code, {e.code for e in raised.exception.errors})

    def test_also_against_every_route_is_unsatisfiable(self):
        with self.assertRaises(GraphInvalid) as raised:  # the closed covers are seen only with M3 at andor
            parse_graph(_strict(see="dark", via={"covercalibrator": "close"}, also={"tertiary": "beso"}))
        self.assertIn("unsatisfiable_path", {e.code for e in raised.exception.errors})


class TestAwaits(unittest.TestCase):
    """Light that comes by itself (the sky) leaves the goal only *not yet* possible, with the moves that
    prepare the path; a lamp never comes by itself."""

    def setUp(self):
        self.g = parse_graph(jk15())

    def dusk(self, rising=False, sun=-12.0, **positions):
        return night(sun_alt_deg=sun, **positions).with_environment(sun_rising=rising)

    def test_object_at_dusk_waits_and_prepares(self):
        verdict = check(self.g, self.dusk(covercalibrator="close"), "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertEqual((verdict.awaits, verdict.prepare), ("sky.science", {"covercalibrator": "open"}))
        self.assertEqual(verdict.reason, "sky.science is not available yet: it comes when the sun sets below -18° (now -12.0°)")

    def test_a_prepared_path_only_waits(self):
        verdict = check(self.g, self.dusk(), "camera", "object")
        self.assertEqual((verdict.awaits, verdict.prepare), ("sky.science", {}))

    def test_the_morning_promises_no_night(self):
        verdict = check(self.g, self.dusk(rising=True), "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertIsNone(verdict.awaits)

    def test_without_the_suns_direction_nothing_is_promised(self):
        self.assertIsNone(check(self.g, night(sun_alt_deg=-12.0), "camera", "object").awaits)

    def test_morning_flats_wait_for_the_dawn(self):
        verdict = check(self.g, self.dusk(rising=True, sun=-17.0), "camera", "skyflat")
        self.assertEqual(verdict.awaits, "sky.flat")
        self.assertIn("rises above -15°", verdict.reason)

    def test_a_preparation_that_collides_is_not_offered(self):
        state = self.dusk(covercalibrator="close")
        state = ProvenState(selectors=state.selectors, environment=state.environment, holds={"covercalibrator": Hold("close", "an operator")})
        verdict = check(self.g, state, "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertIsNone(verdict.awaits)  # the truth stays "not available now", no promise of a path

    def test_the_flat_screen_is_never_awaited(self):
        verdict = check(self.g, self.dusk(flatscreen="off"), "camera", "domeflat")
        self.assertIsInstance(verdict, (Impossible, Collision, Settable))
        self.assertIsNone(getattr(verdict, "awaits", None))


if __name__ == "__main__":
    unittest.main()
