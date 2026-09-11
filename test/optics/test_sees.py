import unittest

from datamodels.optics import DARK, UNDEFINED, SeesRecord, SelectorState

from obcom.optics import ProvenState, available_classes, parse_graph, proven_position, sees
from test.optics.fixtures import BESO, JK15_WITH_MOUNT, TMMT, jk15, night


def classes(records) -> set[tuple[str, str]]:
    return {(r.light_class, r.terminal) for r in records}


def positions_of(state: ProvenState) -> dict:
    return {k: v.position for k, v in state.selectors.items()}


class TestSees(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())

    def test_night_imaging_with_full_provenance(self):
        (record,) = sees(self.g, night(), "camera")
        self.assertEqual(record, SeesRecord(light_class="sky.science", terminal="sky", via=("dome", "covercalibrator", "tertiary", "derotator", "pickoff", "filterwheel")))

    def test_splitter_feeds_both_outputs(self):
        self.assertEqual(classes(sees(self.g, night(), "guider")), {("sky.science", "sky")})
        self.assertEqual(classes(sees(self.g, night(), "camera")), {("sky.science", "sky")})

    def test_other_branch_looks_at_the_back_of_m3(self):
        (record,) = sees(self.g, night(), "guider_beso")
        self.assertEqual(record, SeesRecord(light_class=DARK, terminal="tertiary", via=()))

    def test_m3_readback_unmapped_is_undefined_oca_problems_107(self):
        state = ProvenState(selectors={**night().selectors, "tertiary": SelectorState(position=None, raw=2)}, environment=night().environment)
        (record,) = sees(self.g, state, "camera")
        self.assertEqual(record, SeesRecord(light_class=UNDEFINED, terminal="tertiary", via=("derotator", "pickoff", "filterwheel")))
        self.assertEqual(classes(sees(self.g, state, "guider_beso")), {(UNDEFINED, "tertiary")})

    def test_symbol_not_in_declared_vocabulary_is_undefined(self):
        self.assertEqual(classes(sees(self.g, night(tertiary="nasmyth3"), "camera")), {(UNDEFINED, "tertiary")})

    def test_moving_and_stale_are_not_proven(self):
        moving = ProvenState.build(positions_of(night()), moving=["tertiary"], sun_alt_deg=-30)
        self.assertEqual(classes(sees(self.g, moving, "camera")), {(UNDEFINED, "tertiary")})
        stale = ProvenState.build(positions_of(night()), stale=["covercalibrator"], sun_alt_deg=-30)
        self.assertEqual(classes(sees(self.g, stale, "camera")), {(UNDEFINED, "covercalibrator")})

    def test_selector_without_any_telemetry_is_undefined(self):
        state = ProvenState.build({"covercalibrator": "open", "dome": "open"}, sun_alt_deg=-30)
        self.assertEqual(classes(sees(self.g, state, "camera")), {(UNDEFINED, "tertiary")})

    def test_walk_stops_at_the_first_undefined_selector_from_the_detector(self):
        state = night(tertiary="andor", covercalibrator=None)
        self.assertEqual(classes(sees(self.g, state, "camera")), {(UNDEFINED, "covercalibrator")})

    def test_cover_open_and_calibrator_on_is_a_set(self):
        """Alpaca ICoverCalibrator has two independent axes; the scalar model blessed this BIAS."""
        state = night(**{"covercalibrator.calibrator": "on"})
        self.assertEqual(classes(sees(self.g, state, "camera")), {("sky.science", "sky"), ("lamp", "covercalibrator")})

    def test_closed_cover_is_dark_at_the_cover(self):
        self.assertEqual(classes(sees(self.g, night(covercalibrator="close"), "camera")), {(DARK, "covercalibrator")})
        self.assertEqual(classes(sees(self.g, night(covercalibrator="close"), "guider_beso")), {(DARK, "tertiary")})

    def test_closed_cover_with_lamp_on_is_lamp_not_dark(self):
        state = night(covercalibrator="close", **{"covercalibrator.calibrator": "on"})
        self.assertEqual(classes(sees(self.g, state, "camera")), {("lamp", "covercalibrator")})

    def test_unknown_cover_keeps_a_proven_lamp(self):
        """The calibrator is an independent axis: proven on, its light reaches the detector whatever the cover does."""
        state = night(covercalibrator=None, **{"covercalibrator.calibrator": "on"})
        self.assertEqual(classes(sees(self.g, state, "camera")), {(UNDEFINED, "covercalibrator"), ("lamp", "covercalibrator")})

    def test_unreported_or_unusable_aspect_is_undefined(self):
        no_lamp_telemetry = ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "dome": "open"}, sun_alt_deg=-30)
        self.assertEqual(classes(sees(self.g, no_lamp_telemetry, "camera")), {("sky.science", "sky"), (UNDEFINED, "covercalibrator")})
        stale_lamp = ProvenState(selectors={**night().selectors, "covercalibrator.calibrator": SelectorState(position=None, stale=True)}, environment=night().environment)
        self.assertEqual(classes(sees(self.g, stale_lamp, "camera")), {("sky.science", "sky"), (UNDEFINED, "covercalibrator")})

    def test_idle_branch_sees_dark_and_may_take_darks(self):
        state = night(tertiary="beso")
        self.assertEqual(classes(sees(self.g, state, "camera")), {(DARK, "tertiary")})
        self.assertEqual(classes(sees(self.g, state, "guider_beso")), {("sky.science", "sky")})

    def test_sky_is_a_stateful_source(self):
        for sun, expected in [(-30, "sky.science"), (-16, "sky.twilight"), (-10, "sky.flat"), (0.5, "sky.flat"), (20, "sky.day")]:
            with self.subTest(sun=sun):
                self.assertEqual(classes(sees(self.g, night(sun_alt_deg=sun), "camera")), {(expected, "sky")})

    def test_sky_thresholds_overridable_per_component(self):
        g = parse_graph(jk15(sky={"kind": "sky", "science_sun_alt": -12.0, "flat_sun_alt": [-10.0, 1.0]}))
        self.assertEqual(classes(sees(g, night(sun_alt_deg=-14), "camera")), {("sky.science", "sky")})
        self.assertEqual(classes(sees(g, night(sun_alt_deg=-11), "camera")), {("sky.twilight", "sky")})

    def test_unknown_sun_altitude_is_undefined_at_the_sky(self):
        state = ProvenState.build(positions_of(night()))  # no sun_alt_deg
        self.assertEqual(classes(sees(self.g, state, "camera")), {(UNDEFINED, "sky")})

    def test_dome_closed_and_flat_positions(self):
        self.assertEqual(classes(sees(self.g, night(dome="closed"), "camera")), {(DARK, "dome")})
        self.assertEqual(classes(sees(self.g, night(dome="flat"), "camera")), {("flatscreen", "flatscreen")})

    def test_not_a_detector(self):
        with self.assertRaises(ValueError):
            sees(self.g, night(), "tertiary")
        with self.assertRaises(KeyError):
            sees(self.g, night(), "nonexistent")


MULTIPORT = {
    "sky": {"kind": "sky"},
    "m3": {"kind": "mirror", "positions": {"a": {}, "b": {}, "c": {}}, "optics": {"from": "sky"}},
    "wide": {"kind": "camera", "optics": {"from": {"m3": ["a", "b"]}}, "paths": {"object": "sky.science", "dark": "dark"}},
    "narrow": {"kind": "camera", "optics": {"from": {"m3": "c"}}, "paths": {"object": "sky.science"}},
}


class TestMultiPortEdge(unittest.TestCase):
    """``from: {X: [p1, p2]}`` is one edge live on either port, not two edges one of which is dark."""

    def setUp(self):
        self.g = parse_graph(MULTIPORT)

    def test_selected_port_in_the_set_is_light_only(self):
        for pos in ("a", "b"):
            with self.subTest(pos=pos):
                self.assertEqual(classes(sees(self.g, ProvenState.build({"m3": pos}, sun_alt_deg=-30), "wide")), {("sky.science", "sky")})
                self.assertEqual(classes(sees(self.g, ProvenState.build({"m3": pos}, sun_alt_deg=-30), "narrow")), {(DARK, "m3")})

    def test_position_outside_the_set_is_dark(self):
        state = ProvenState.build({"m3": "c"}, sun_alt_deg=-30)
        self.assertEqual(classes(sees(self.g, state, "wide")), {(DARK, "m3")})
        self.assertEqual(classes(sees(self.g, state, "narrow")), {("sky.science", "sky")})


class TestDerivedDome(unittest.TestCase):
    """The dome is a selector whose position is computed from shutter + pointing (config values
    already there: dome.domeflat_az, mount.domeflat_az_offset / domeflat_alt)."""

    def setUp(self):
        self.g = parse_graph(JK15_WITH_MOUNT)
        self.base = {"tertiary": "andor", "covercalibrator": "open", "covercalibrator.calibrator": "off"}

    def test_shutter_closed_is_dark(self):
        state = ProvenState.build(self.base, sun_alt_deg=-30, dome_shutter_open=False)
        self.assertEqual(classes(sees(self.g, state, "camera")), {(DARK, "dome")})

    def test_at_the_screen_is_flat(self):
        state = ProvenState.build(self.base, sun_alt_deg=10, dome_shutter_open=True, dome_az_deg=49.0, mount_az_deg=229.0, mount_alt_deg=15.0)
        self.assertEqual(classes(sees(self.g, state, "camera")), {("flatscreen", "flatscreen")})
        self.assertEqual(proven_position(self.g, state, self.g.nodes["dome"]), "flat")

    def test_pointing_elsewhere_is_open(self):
        state = ProvenState.build(self.base, sun_alt_deg=-30, dome_shutter_open=True, dome_az_deg=120.0, mount_az_deg=120.0, mount_alt_deg=60.0)
        self.assertEqual(classes(sees(self.g, state, "camera")), {("sky.science", "sky")})

    def test_wrong_altitude_at_the_screen_azimuth_is_open(self):
        state = ProvenState.build(self.base, sun_alt_deg=-30, dome_shutter_open=True, dome_az_deg=49.0, mount_az_deg=229.0, mount_alt_deg=60.0)
        self.assertEqual(proven_position(self.g, state, self.g.nodes["dome"]), "open")

    def test_missing_altitude_target_at_the_screen_azimuth_is_undecidable(self):
        comps = {**JK15_WITH_MOUNT, "mount": {"kind": "telescope", "domeflat_az_offset": 180.0}}
        g = parse_graph(comps)
        state = ProvenState.build(self.base, sun_alt_deg=10, dome_shutter_open=True, dome_az_deg=49.0, mount_az_deg=229.0, mount_alt_deg=15.0)
        self.assertIsNone(proven_position(g, state, g.nodes["dome"]))
        away = state.with_environment(mount_az_deg=100.0, dome_az_deg=100.0)
        self.assertEqual(proven_position(g, away, g.nodes["dome"]), "open")

    def test_missing_inputs_are_undefined(self):
        self.assertIsNone(proven_position(self.g, ProvenState.build(self.base), self.g.nodes["dome"]))
        shutter_only = ProvenState.build(self.base, dome_shutter_open=True)
        self.assertIsNone(proven_position(self.g, shutter_only, self.g.nodes["dome"]))

    def test_explicit_dome_telemetry_wins_over_derivation(self):
        state = ProvenState.build({**self.base, "dome": "open"}, sun_alt_deg=-30, dome_shutter_open=False)
        self.assertEqual(classes(sees(self.g, state, "camera")), {("sky.science", "sky")})

    def test_dome_without_flat_input_needs_only_the_shutter(self):
        g = parse_graph(BESO)
        state = ProvenState.build({"tertiary": "beso", "covercalibrator": "open", "covercalibrator.calibrator": "off", "m4": "sky"}, sun_alt_deg=-30, dome_shutter_open=True)
        self.assertEqual(classes(sees(g, state, "beso")), {("sky.science", "sky")})  # unreported lamps are off the selected path


class TestFanInAndSources(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(BESO)
        self.base = {"tertiary": "beso", "covercalibrator": "open", "covercalibrator.calibrator": "off", "dome": "open"}

    def test_two_real_mirrors_select_the_lamp(self):
        state = ProvenState.build({**self.base, "m4": "calib", "m5": "thar", "thar_lamp": "on"}, sun_alt_deg=-30)
        (record,) = sees(self.g, state, "beso")
        self.assertEqual(record, SeesRecord(light_class="lamp", terminal="thar_lamp", via=("m5", "m4")))
        state = state.with_positions({"m5": "white", "white_lamp": "on"})
        self.assertEqual(classes(sees(self.g, state, "beso")), {("lamp", "white_lamp")})

    def test_parked_mirror_is_dark(self):
        state = ProvenState.build({**self.base, "m4": "park", "m5": "thar"}, sun_alt_deg=-30)
        self.assertEqual(classes(sees(self.g, state, "beso")), {(DARK, "m4")})

    def test_source_telemetry_is_judged_like_a_selector(self):
        base = {**self.base, "m4": "calib", "m5": "thar"}
        for bad in (ProvenState.build({**base}, stale=["thar_lamp"], sun_alt_deg=-30),
                    ProvenState.build({**base}, moving=["thar_lamp"], sun_alt_deg=-30),
                    ProvenState.build({**base, "thar_lamp": None}, sun_alt_deg=-30),
                    ProvenState.build({**base, "thar_lamp": "warming"}, sun_alt_deg=-30)):
            with self.subTest(state=bad.selectors["thar_lamp"]):
                self.assertEqual(classes(sees(self.g, bad, "beso")), {(UNDEFINED, "thar_lamp")})
        stale_on = ProvenState(selectors={**ProvenState.build(base).selectors, "thar_lamp": SelectorState(position="on", stale=True)},
                               environment=ProvenState.build({}, sun_alt_deg=-30).environment)
        self.assertEqual(classes(sees(self.g, stale_on, "beso")), {(UNDEFINED, "thar_lamp")})

    def test_switchable_source_proven_off_is_dark_at_the_lamp(self):
        state = ProvenState.build({**self.base, "m4": "calib", "m5": "thar", "thar_lamp": "off"}, sun_alt_deg=-30)
        self.assertEqual(classes(sees(self.g, state, "beso")), {(DARK, "thar_lamp")})

    def test_fibre_path_to_sky(self):
        state = ProvenState.build({**self.base, "m4": "sky"}, sun_alt_deg=-30)
        (record,) = sees(self.g, state, "beso")
        self.assertEqual(record.via, ("dome", "covercalibrator", "tertiary", "fiber", "m4"))

    def test_available_classes_now(self):
        state = ProvenState.build({**self.base, "m4": "calib", "m5": "thar", "thar_lamp": "off", "white_lamp": "on"}, sun_alt_deg=-30)
        self.assertEqual(available_classes(self.g, state), frozenset({"sky.science", "lamp"}))
        self.assertEqual(available_classes(self.g, ProvenState.build({**self.base, "thar_lamp": "off", "white_lamp": "off"}, sun_alt_deg=-30)), frozenset({"sky.science"}))
        self.assertEqual(available_classes(self.g, ProvenState.build({})), frozenset())  # nothing proven ⇒ nothing available

    def test_tmmt_without_selectors(self):
        g = parse_graph(TMMT)
        state = ProvenState.build({}, sun_alt_deg=-30)
        for cam in g.detectors:
            self.assertEqual(classes(sees(g, state, cam)), {("sky.science", "sky")})


if __name__ == "__main__":
    unittest.main()
