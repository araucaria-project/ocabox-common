import unittest

from datamodels.optics import Archetype, Invalid, TelescopeOpticsSpec

from obcom.optics import (
    enumerate_routes,
    DEFAULT_REGISTRY,
    IN,
    OUT,
    GraphInvalid,
    KindRegistry,
    Passive,
    SelectorShape,
    parse_graph,
    validate_graph,
)
from test.optics.fixtures import BESO, TMMT, jk15


def codes(exc: GraphInvalid) -> set[str]:
    return {e.code for e in exc.errors}


class TestParseGraph(unittest.TestCase):

    def test_jk15_parses_with_archetypes_from_kind(self):
        g = parse_graph(jk15())
        self.assertEqual(set(g.detectors), {"camera", "guider", "guider_beso"})
        self.assertEqual(set(g.selectors), {"dome", "covercalibrator", "tertiary"})
        self.assertEqual(set(g.sources), {"sky", "flatscreen"})
        self.assertEqual(g.nodes["pickoff"].archetype, Archetype.SPLITTER)
        self.assertEqual(g.nodes["derotator"].archetype, Archetype.PASSIVE)

    def test_selector_shapes_are_resolved_per_component(self):
        g = parse_graph(jk15())
        self.assertEqual(g.nodes["tertiary"].shape, SelectorShape.OUTPUT_PORTS)
        self.assertEqual(g.nodes["dome"].shape, SelectorShape.FAN_IN)
        self.assertEqual(g.nodes["covercalibrator"].shape, SelectorShape.GATE)
        self.assertEqual(g.nodes["tertiary"].positions, frozenset({"beso", "andor"}))
        self.assertEqual(g.nodes["dome"].positions, frozenset({"open", "flat", "closed"}))
        self.assertEqual(g.nodes["covercalibrator"].positions, frozenset({"open", "close"}))
        self.assertEqual(g.nodes["dome"].dark_positions(), frozenset({"closed"}))
        self.assertEqual(g.nodes["covercalibrator"].transmitting_positions(), frozenset({"open"}))

    def test_same_mirror_kind_is_fan_in_when_it_declares_inputs(self):
        g = parse_graph(BESO)
        self.assertEqual(g.nodes["m4"].shape, SelectorShape.FAN_IN)
        self.assertEqual(g.nodes["m4"].fan_in, {"sky": "fiber", "calib": "m5"})
        self.assertEqual(g.nodes["m4"].dark_positions(), frozenset({"park"}))
        self.assertEqual(g.nodes["tertiary"].shape, SelectorShape.OUTPUT_PORTS)

    def test_edges_normalise_to_feeds_per_input_port(self):
        g = parse_graph(jk15())
        (feed,) = g.nodes["derotator"].feeds[IN]
        self.assertEqual((feed.upstream, feed.ports), ("tertiary", frozenset({"andor"})))
        self.assertEqual({port: fs[0].upstream for port, fs in g.nodes["dome"].feeds.items()}, {"open": "sky", "flat": "flatscreen"})
        (passive,) = g.nodes["covercalibrator"].feeds[IN]
        self.assertEqual((passive.upstream, passive.ports), ("dome", frozenset({OUT})))
        self.assertEqual(g.nodes["dome"].fan_in, {"open": "sky", "flat": "flatscreen"})

    def test_upstream_cone(self):
        g = parse_graph(jk15())
        self.assertEqual(g.upstream_cone("guider_beso"), frozenset({"tertiary", "covercalibrator", "dome", "sky", "flatscreen"}))
        self.assertIn("derotator", g.upstream_cone("camera"))
        self.assertNotIn("guider", g.upstream_cone("camera"))

    def test_non_optical_components_ride_along(self):
        comps = jk15(mount={"kind": "telescope", "device_number": 0}, focuser={"kind": "focuser"})
        g = parse_graph(comps)
        self.assertNotIn("mount", g.nodes)
        self.assertIn("mount", g.components)

    def test_optical_kind_without_optics_block_is_not_authored_yet(self):
        comps = jk15(spare={"kind": "camera", "device_number": 3})
        self.assertNotIn("spare", parse_graph(comps).nodes)

    def test_tmmt_has_no_selectors(self):
        g = parse_graph(TMMT)
        self.assertEqual(g.selectors, ())
        self.assertEqual(len(g.detectors), 3)

    def test_validate_graph_returns_invalid_verdict(self):
        verdict = validate_graph(jk15(derotator={"kind": "rotator", "optics": {"from": {"tertiary": "nasmyth3"}}}))
        self.assertIsInstance(verdict, Invalid)
        self.assertEqual(verdict.errors[0].code, "undeclared_port")
        self.assertIsNone(validate_graph(jk15()))

    def test_custom_registry(self):
        reg = KindRegistry()
        reg.register(Passive(name="mystery"))
        comps = {"sky": {"kind": "sky"}, "x": {"kind": "mystery", "optics": {"from": "sky"}}}
        with self.assertRaises(GraphInvalid) as cm:
            parse_graph(comps, registry=reg)  # sky is unknown to the empty registry
        self.assertEqual(codes(cm.exception), {"unknown_kind"})
        reg2 = DEFAULT_REGISTRY.copy()
        reg2.register(Passive(name="mystery"))
        self.assertEqual(parse_graph(comps, registry=reg2).nodes["x"].archetype, Archetype.PASSIVE)


class TestLoadTimeValidation(unittest.TestCase):
    """Every red-team rule kills a real failure; each must fail at config load with a reason."""

    def assertInvalid(self, comps, *expected_codes, component=None):
        with self.assertRaises(GraphInvalid) as cm:
            parse_graph(comps)
        self.assertEqual(codes(cm.exception), set(expected_codes), cm.exception.errors)
        if component is not None:
            self.assertIn(component, {e.component for e in cm.exception.errors})
        return cm.exception

    def test_undeclared_port_phantom_typo(self):
        exc = self.assertInvalid(jk15(derotator={"kind": "rotator", "optics": {"from": {"tertiary": "nasmyth3"}}}), "undeclared_port", component="derotator")
        self.assertIn("declared: andor, beso", exc.errors[0].message)

    def test_duplicate_from_on_one_port_needs_a_splitter(self):
        comps = jk15(guider={"kind": "camera", "optics": {"from": {"tertiary": "andor"}}})
        self.assertInvalid(comps, "duplicate_from", component="guider")

    def test_emitted_classes_are_light_for_every_kind(self):
        extra = {"dump": {"kind": "beamdump"}, "lamp2": {"kind": "lamp"},
                 "m6": {"kind": "mirror", "positions": {"a": {}, "b": {}}, "optics": {"inputs": {"a": "dump", "b": "lamp2"}}}}
        g = parse_graph({**BESO, **extra})
        self.assertEqual(g.emitted_classes("lamp2"), frozenset({"lamp"}))  # off ⇒ dark is a state, not a light class
        self.assertEqual(g.emitted_classes("dump"), frozenset())
        self.assertEqual(g.emitted_classes("covercalibrator"), frozenset({"lamp"}))

    def test_a_lamp_has_one_output_but_ambient_sources_have_many(self):
        comps = {**BESO, "second": {"kind": "spectrograph", "optics": {"from": "thar_lamp"}}}
        self.assertInvalid(comps, "duplicate_from", component="second")
        parse_graph({**TMMT, "cam_d": {"kind": "camera", "optics": {"from": "sky"}}})  # the sky feeds any number of apertures

    def test_duplicate_passive_edge_on_single_output(self):
        comps = jk15(extra={"kind": "camera", "optics": {"from": "filterwheel"}})
        self.assertInvalid(comps, "duplicate_from")

    def test_several_components_may_hang_on_a_source(self):
        parse_graph(TMMT)  # sky feeds three cameras — ambient light, not a port

    def test_port_required_on_output_port_selector(self):
        self.assertInvalid(jk15(derotator={"kind": "rotator", "optics": {"from": "tertiary"}}), "port_required")

    def test_no_output_ports_on_gate_and_fan_in(self):
        self.assertInvalid(jk15(tertiary={**jk15()["tertiary"], "optics": {"from": {"covercalibrator": "open"}}}), "no_output_ports")
        self.assertInvalid(jk15(covercalibrator={"kind": "covercalibrator", "optics": {"from": {"dome": "open"}}}), "no_output_ports")

    def test_not_a_switch(self):
        self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": {"filterwheel": "r"}}}), "not_a_switch")

    def test_unknown_component(self):
        self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": "filterwhel"}}), "unknown_component", component="camera")

    def test_unknown_kind_with_optics(self):
        self.assertInvalid(jk15(focuser={"kind": "focuser", "optics": {"from": "derotator"}}), "unknown_kind", component="focuser")

    def test_reference_to_non_optical_kind(self):
        comps = jk15(mount={"kind": "telescope"}, camera={"kind": "camera", "optics": {"from": "mount"}})
        self.assertInvalid(comps, "unknown_kind")

    def test_source_has_input(self):
        # hanging the sky on the pick-off also closes a loop: both problems are reported together
        self.assertInvalid(jk15(sky={"kind": "sky", "optics": {"from": {"pickoff": "third"}}}), "source_has_input", "cycle", component="sky")

    def test_structural_and_cycle_errors_are_reported_together(self):
        comps = jk15(
            derotator={"kind": "rotator", "optics": {"from": "filterwheel"}}, camera=None,  # cycle
            guider_beso={"kind": "camera", "optics": {"from": {"tertiary": "nasmyth3"}}},  # undeclared port
        )
        self.assertInvalid(comps, "cycle", "undeclared_port")

    def test_fan_in_cannot_take_a_multi_output_selector_without_its_port(self):
        comps = jk15(m4={"kind": "mirror", "optics": {"inputs": {"sky": "tertiary", "calib": "flatscreen"}}},
                     beso={"kind": "spectrograph", "optics": {"from": "m4"}})
        exc = self.assertInvalid(comps, "port_required", component="m4")
        self.assertIn("from: {tertiary: <symbol>}", exc.errors[0].message)

    def test_detector_as_upstream(self):
        self.assertInvalid(jk15(extra={"kind": "camera", "optics": {"from": "guider"}}), "detector_as_upstream")

    def test_missing_input_light_from_nowhere(self):
        self.assertInvalid(jk15(filterwheel={"kind": "filterwheel"}), "missing_input", component="filterwheel")

    def test_paths_without_optics(self):
        self.assertInvalid(jk15(spare={"kind": "camera", "paths": {"object": "sky.science"}}), "paths_without_optics")

    def test_paths_on_non_detector(self):
        self.assertInvalid(jk15(derotator={"kind": "rotator", "optics": {"from": {"tertiary": "andor"}}, "paths": {"object": "sky.science"}}), "paths_on_non_detector")

    def test_inputs_on_non_selector(self):
        self.assertInvalid(jk15(derotator={"kind": "rotator", "optics": {"inputs": {"a": "tertiary"}}}), "inputs_on_non_selector")

    def test_dome_inputs_are_fixed_by_the_kind(self):
        self.assertInvalid(jk15(dome={"kind": "dome", "optics": {"inputs": {"sky": "sky"}}}), "undeclared_position", "invalid_option", component="dome")  # `sky` is no dome input, and `open` is missing

    def test_fan_in_input_must_be_a_declared_position(self):
        comps = dict(BESO)
        comps["m4"] = {"kind": "mirror", "positions": {"sky": {}, "park": {}}, "optics": {"inputs": {"sky": "fiber", "calib": "m5"}}}
        self.assertInvalid(comps, "undeclared_position", component="m4")

    def test_cycle(self):
        # derotator -> pickoff -> filterwheel -> derotator, with the camera removed so nothing else hangs on the wheel
        comps = jk15(derotator={"kind": "rotator", "optics": {"from": "filterwheel"}}, camera=None)
        exc = self.assertInvalid(comps, "cycle")
        self.assertIn("->", exc.errors[0].message)

    def test_unsatisfiable_path_no_such_source_upstream(self):
        self.assertInvalid(jk15(guider_beso={"kind": "camera", "optics": {"from": {"tertiary": "beso"}}, "paths": {"arc": "lamp.thar"}}), "unsatisfiable_path")

    def test_unsatisfiable_path_via_contradicts_route(self):
        paths = {"dark": {"see": "dark", "via": {"tertiary": "andor", "covercalibrator": "open", "dome": "open"}}}
        comps = jk15(camera={"kind": "camera", "optics": {"from": "filterwheel"}, "paths": paths})
        self.assertInvalid(comps, "unsatisfiable_path")

    def test_via_outside_cone(self):
        paths = {"dark": {"see": "dark", "via": {"pickoff": "main"}}}  # a splitter is not a selector
        self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": "filterwheel"}, "paths": paths}), "via_outside_cone")
        paths = {"dark": {"see": "dark", "via": {"tertiary": "andor"}}}
        comps = jk15(guider_beso={"kind": "camera", "optics": {"from": {"tertiary": "beso"}}}, cam2={"kind": "camera", "optics": {"from": "sky"}, "paths": paths})
        self.assertInvalid(comps, "via_outside_cone")

    def test_via_undeclared_position_and_unknown_aspect(self):
        paths = {"dark": {"see": "dark", "via": {"covercalibrator": "shut"}}}
        self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": "filterwheel"}, "paths": paths}), "undeclared_position")
        paths = {"flat": {"see": "lamp", "via": {"covercalibrator.lamp": "on"}}}
        self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": "filterwheel"}, "paths": paths}), "unknown_aspect")
        paths = {"flat": {"see": "lamp", "via": {"covercalibrator.calibrator": "lit"}}}
        self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": "filterwheel"}, "paths": paths}), "undeclared_position")

    def test_when_must_name_a_class_the_telescope_can_emit(self):
        paths = {"dark": {"see": "dark", "when": "sky.eclipse"}}
        self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": "filterwheel"}, "paths": paths}), "unknown_light_class")
        paths = {"zero": [{"see": "dark", "when": "dark"}]}  # `when` is judged against available light; dark never applies
        self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": "filterwheel"}, "paths": paths}), "unknown_light_class")

    def test_presets_must_name_a_detector_and_one_of_its_paths(self):
        with self.assertRaises(GraphInvalid) as cm:
            parse_graph(jk15(), presets={"imaging": {"camera": "object"}, "bad": {"beso": "object"}, "worse": {"camera": "spectroscopy"}})
        self.assertEqual(codes(cm.exception), {"preset_unknown_detector", "preset_unknown_path"})
        self.assertEqual({e.path for e in cm.exception.errors}, {"presets.bad.beso", "presets.worse.camera"})
        g = parse_graph(jk15(), presets={"imaging": {"camera": "object"}, "calibration": {"camera": "dark"}})
        self.assertEqual(set(g.presets), {"imaging", "calibration"})

    def test_kind_options_are_validated_at_load(self):
        self.assertInvalid(jk15(sky={"kind": "sky", "flat_sun_alt": -10}), "invalid_option", component="sky")
        self.assertInvalid(jk15(sky={"kind": "sky", "science_sun_alt": "dark"}), "invalid_option", component="sky")
        self.assertInvalid(jk15(sky={"kind": "sky", "science_sun_alt": -5.0}), "invalid_option", component="sky")  # above the flat range
        self.assertInvalid(jk15(sky={"kind": "sky", "flat_sun_alt": [-10.0, float("inf")]}), "invalid_option", component="sky")
        self.assertInvalid(jk15(dome={**jk15()["dome"], "slew_tolerance": "3"}), "invalid_option", component="dome")
        self.assertInvalid(jk15(dome={**jk15()["dome"], "slew_tolerance": -1}), "invalid_option", component="dome")
        self.assertInvalid(jk15(dome={**jk15()["dome"], "slew_tolerance": float("nan")}), "invalid_option", component="dome")
        fw = {"kind": "filterwheel", "positions": {"v": {"slot": 1}, "blank": {"slot": 2, "dark": "false"}}, "optics": {"from": {"pickoff": "main"}}}
        self.assertInvalid(jk15(filterwheel=fw), "invalid_option", component="filterwheel")
        fw_null = {**fw, "positions": {"v": {"slot": 1}, "blank": {"slot": 2, "dark": None}}}
        self.assertInvalid(jk15(filterwheel=fw_null), "invalid_option", component="filterwheel")  # `dark: null` is not "no flag"
        dome = jk15()["dome"]
        self.assertInvalid(jk15(dome={**dome, "optics": {"inputs": {"flat": "flatscreen"}}}), "invalid_option", component="dome")  # no `open`: derivation would emit dark
        self.assertInvalid(jk15(dome={**dome, "positions": {"open": {}, "flat": {}, "shut": {}}}), "invalid_option", component="dome")
        cover = {"kind": "covercalibrator", "positions": {"open": {"code": 1}, "closed": {"code": 2}}, "optics": {"from": "dome"}}
        exc = self.assertInvalid(jk15(covercalibrator=cover), "invalid_option", component="covercalibrator")
        self.assertIn("knows only open, close", exc.errors[0].message)

    def test_presets_come_in_one_form_only(self):
        spec = TelescopeOpticsSpec.model_validate({"components": jk15(), "presets": {"imaging": {"camera": "object"}}})
        self.assertEqual(set(parse_graph(spec).presets), {"imaging"})
        with self.assertRaises(ValueError):
            parse_graph(spec, presets={"calibration": {"camera": "dark"}})
        self.assertInvalid(jk15(mount={"kind": "telescope", "domeflat_alt": True}), "invalid_option", component="dome")
        parse_graph(jk15(sky={"kind": "sky", "science_sun_alt": -12, "flat_sun_alt": [-10, 2]}))

    def test_grammar_errors_are_reported_with_location(self):
        exc = self.assertInvalid(jk15(camera={"kind": "camera", "optics": {"from": ["a", "b"]}}), "grammar", component="camera")
        self.assertTrue(exc.errors[0].path.startswith("components.camera.optics"))

    def test_every_error_is_reported_not_just_the_first(self):
        comps = jk15(
            derotator={"kind": "rotator", "optics": {"from": {"tertiary": "nasmyth3"}}},
            camera={"kind": "camera", "optics": {"from": "filterwhel"}},
        )
        self.assertInvalid(comps, "undeclared_port", "unknown_component")


class TestKindPromotion(unittest.TestCase):

    def test_filterwheel_with_dark_slide_becomes_a_gate(self):
        fw = {"kind": "filterwheel", "positions": {"v": {"slot": 1}, "closed": {"slot": 7, "dark": True}}, "optics": {"from": {"pickoff": "main"}}}
        g = parse_graph(jk15(filterwheel=fw))
        node = g.nodes["filterwheel"]
        self.assertEqual(node.archetype, Archetype.SELECTOR)
        self.assertEqual(node.shape, SelectorShape.GATE)
        self.assertEqual(node.dark_positions(), frozenset({"closed"}))
        self.assertIn("filterwheel", g.selectors)

    def test_filterwheel_without_dark_position_stays_passive(self):
        fw = {"kind": "filterwheel", "positions": {"v": {"slot": 1}, "r": {"slot": 2}}, "optics": {"from": {"pickoff": "main"}}}
        self.assertEqual(parse_graph(jk15(filterwheel=fw)).nodes["filterwheel"].archetype, Archetype.PASSIVE)


class TestAuthoredOrder(unittest.TestCase):

    def test_vocabularies_keep_the_authored_order(self):
        g = parse_graph(jk15())
        self.assertEqual(g.nodes["tertiary"].primary.vocabulary, ("beso", "andor"))
        self.assertEqual(g.nodes["covercalibrator"].primary.vocabulary, ("open", "close"))
        self.assertEqual(g.nodes["dome"].primary.vocabulary, ("open", "flat", "closed"))  # authored inputs, then the intrinsic closed
        self.assertEqual([row[None] for row in g.nodes["tertiary"].assignments()], ["beso", "andor"])

    def test_routes_enumerate_in_authored_order(self):
        g = parse_graph(jk15())
        terminals = [r.terminal for r in enumerate_routes(g, "camera") if "dark" in r.classes]
        # table rows in authored order, nearest component first: M3 away (beso before andor), then through the
        # cover's first row (open) to the dome's rows, then the cover's own closed row
        self.assertEqual(terminals, ["tertiary", "dome", "covercalibrator"])
        swapped = jk15(tertiary={**jk15()["tertiary"], "positions": {"andor": {"port": 2}, "beso": {"port": 1}}})
        self.assertEqual(parse_graph(swapped).nodes["tertiary"].primary.vocabulary, ("andor", "beso"))


if __name__ == "__main__":
    unittest.main()
