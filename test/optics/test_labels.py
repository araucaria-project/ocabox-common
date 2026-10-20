import unittest

from datamodels.optics import Active, Collision, Impossible

from obcom.optics import Declaration, Hold, ProvenState, check, parse_graph, resolve_axes
from obcom.optics.labels import component_label, label
from test.optics.fixtures import BESO, jk15, night


class TestLabels(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())

    def test_the_only_component_of_a_kind_takes_the_kind_name(self):
        self.assertEqual(label(self.g, "covercalibrator"), "mirror covers")
        self.assertEqual(label(self.g, "covercalibrator.calibrator"), "calibration lamp")
        self.assertEqual(label(self.g, "tertiary"), "M3")

    def test_components_sharing_a_kind_keep_their_own_names(self):
        g = parse_graph(BESO)
        self.assertEqual(component_label(g, "thar_lamp"), "thar lamp")
        self.assertEqual(component_label(g, "white_lamp"), "white lamp")

    def test_display_labels_from_the_config_win(self):
        components = jk15()
        components["covercalibrator"] = {**components["covercalibrator"],
                                         "display": {"label": "primary covers", "aspects": {"calibrator": "flat lamp"}}}
        g = parse_graph(components)
        self.assertEqual(label(g, "covercalibrator"), "primary covers")
        self.assertEqual(label(g, "covercalibrator.calibrator"), "flat lamp")


class TestCauses(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())

    def test_a_broken_shutter_is_named_not_the_dome(self):
        state = ProvenState.build({"covercalibrator": "open", "covercalibrator.calibrator": "off", "tertiary": "andor"},
                                  sun_alt_deg=-30, dome_az_deg=180.0, mount_az_deg=0.0, mount_alt_deg=45.0)
        self.assertEqual(resolve_axes(self.g, state, self.g.nodes["dome"])[None].cause, "shutter state unknown")

    def test_the_reader_cause_wins(self):
        state = ProvenState.build({"covercalibrator": "open"}, sun_alt_deg=-30,
                                  causes={"dome": "shutter reports an error (ShutterStatus 4)"})
        self.assertEqual(resolve_axes(self.g, state, self.g.nodes["dome"])[None].cause, "shutter reports an error (ShutterStatus 4)")

    def test_unusable_telemetry_says_why(self):
        state = ProvenState.build({"tertiary": "andor"}, moving=["tertiary"], sun_alt_deg=-30)
        self.assertEqual(resolve_axes(self.g, state, self.g.nodes["tertiary"])[None].cause, "moving")
        state = ProvenState.build({"tertiary": "nowhere"}, sun_alt_deg=-30)
        self.assertEqual(resolve_axes(self.g, state, self.g.nodes["tertiary"])[None].cause, "its sensor reports 'nowhere', not a known position")

    def test_reasons_speak_of_what_people_see(self):
        verdict = check(self.g, ProvenState(night(tertiary="beso").selectors, night().environment,
                                            holds={"tertiary": Hold("beso", "beso-run")}), "camera", "object")
        self.assertIsInstance(verdict, Collision)
        self.assertEqual(verdict.reason, "sky.science needs M3 andor, it is beso — held by beso-run")

    def test_unknown_axes_are_listed_with_their_causes(self):
        state = ProvenState(night().selectors, night().environment.model_copy(update={"sun_alt_deg": None}))
        verdict = check(self.g, state, "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertIn("unknown: sky (sun altitude unknown)", verdict.reason)

    def test_a_held_unknown_selector_names_its_cause_and_holder(self):
        state = ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "covercalibrator.calibrator": "off"},
                                  sun_alt_deg=-30, holds={"dome": Hold(None, "its own controls")},
                                  causes={"dome": "shutter reports an error (ShutterStatus 4)"})
        verdict = check(self.g, state, "camera", "object")
        self.assertIsInstance(verdict, Impossible)
        self.assertEqual(verdict.reason, "dome: position unknown (shutter reports an error (ShutterStatus 4)), "
                                         "cannot be moved — held by its own controls")


class TestDeclarations(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())
        self.broken_shutter = dict(sun_alt_deg=-30, causes={"dome": "shutter reports an error (ShutterStatus 4)"})

    def test_a_declared_position_fills_an_undefined_axis(self):
        state = ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "covercalibrator.calibrator": "off"},
                                  declared={"dome": Declaration("open", "mka")}, **self.broken_shutter)
        resolved = resolve_axes(self.g, state, self.g.nodes["dome"])[None]
        self.assertEqual((resolved.value, resolved.origin, resolved.by), ("open", "declared", "mka"))
        self.assertEqual(resolved.cause, "shutter reports an error (ShutterStatus 4)")  # still says why no reading proves it
        self.assertIsInstance(check(self.g, state, "camera", "object"), Active)

    def test_a_reading_always_wins_over_a_declaration(self):
        state = ProvenState.build({"tertiary": "beso"}, sun_alt_deg=-30, declared={"tertiary": Declaration("andor", "mka")})
        self.assertEqual(resolve_axes(self.g, state, self.g.nodes["tertiary"])[None].origin, "telemetry")
        self.assertEqual(resolve_axes(self.g, state, self.g.nodes["tertiary"])[None].value, "beso")

    def test_a_declaration_outside_the_vocabulary_proves_nothing(self):
        state = ProvenState.build({}, declared={"dome": Declaration("ajar", "mka")}, **self.broken_shutter)
        self.assertIsNone(resolve_axes(self.g, state, self.g.nodes["dome"])[None].value)
