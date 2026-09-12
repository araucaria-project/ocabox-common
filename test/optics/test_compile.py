import unittest

from datamodels.optics import OpticsCompiled, Route

from obcom.optics import CompileError, authored_hash, compile_observatory, compile_telescope, enumerate_routes, parse_graph
from obcom.optics.compile import _verify
from test.optics.fixtures import BESO, TMMT, jk15


def with_beso_paths() -> dict:
    return jk15(guider_beso={"kind": "camera", "optics": {"from": {"tertiary": "beso"}}, "paths": {"object": "sky.science", "dark": "dark"}})


class TestStaticRoutes(unittest.TestCase):

    def test_every_way_light_reaches_the_camera(self):
        g = parse_graph(jk15())
        routes = enumerate_routes(g, "camera")
        by_terminal = {}
        for r in routes:
            by_terminal.setdefault(r.terminal, []).append(r)
        self.assertEqual(set(by_terminal), {"sky", "flatscreen", "dome", "covercalibrator", "tertiary"})
        sky = by_terminal["sky"][0]
        self.assertEqual(sky.positions, {"dome": "open", "covercalibrator": "open", "covercalibrator.calibrator": "off", "tertiary": "andor"})
        self.assertEqual(sky.via, ("dome", "covercalibrator", "tertiary", "derotator", "pickoff", "filterwheel"))
        self.assertTrue({"sky.science", "sky.flat", "sky.day", "sky.twilight"} <= sky.classes)
        lamp = [r for r in by_terminal["covercalibrator"] if "lamp" in r.classes]
        self.assertEqual(len(lamp), 1)
        self.assertEqual(lamp[0].positions, {"covercalibrator": "close", "covercalibrator.calibrator": "on", "tertiary": "andor"})

    def test_fan_in_routes_pin_both_mirrors(self):
        g = parse_graph(BESO)
        routes = enumerate_routes(g, "beso")
        thar = next(r for r in routes if r.terminal == "thar_lamp")
        self.assertEqual(thar.positions, {"m4": "calib", "m5": "thar"})
        self.assertEqual(thar.via, ("m5", "m4"))
        self.assertIn({"m4": "park"}, [dict(r.positions) for r in routes if r.terminal == "m4"])


class TestMultiPortRoutes(unittest.TestCase):

    def test_one_transmitting_route_per_allowed_port_and_dark_only_outside(self):
        from test.optics.test_sees import MULTIPORT
        g = parse_graph(MULTIPORT)
        routes = enumerate_routes(g, "wide")
        light = sorted(r.positions["m3"] for r in routes if "sky.science" in r.classes)
        dark = sorted(r.positions["m3"] for r in routes if r.classes == frozenset({"dark"}))
        self.assertEqual((light, dark), (["a", "b"], ["c"]))
        compiled = compile_telescope(g)  # every route replays through sees()
        self.assertEqual(sorted(r.positions["m3"] for r in compiled.routes if r.detector == "wide" and r.function == "object"), ["a", "b"])


class TestCompile(unittest.TestCase):

    def test_route_table_covers_every_alternative(self):
        compiled = compile_telescope(parse_graph(jk15()))
        self.assertEqual(compiled.selectors, ["dome", "covercalibrator", "tertiary"])
        self.assertEqual(set(compiled.detectors), {"camera", "guider", "guider_beso"})
        keys = {(r.function, r.alternative) for r in compiled.routes}
        self.assertEqual(keys, {("object", 0), ("snap", 0), ("focus", 0), ("skyflat", 0), ("domeflat", 0), ("zero", 0), ("dark", 0), ("dark_strict", 0), ("dark_strict", 1)})
        strict = [r for r in compiled.routes if r.function == "dark_strict"]
        self.assertEqual(strict[0].positions, {"covercalibrator": "close", "covercalibrator.calibrator": "off", "tertiary": "andor"})
        self.assertEqual(strict[0].when, "sky.science")
        self.assertEqual(strict[1].positions, {"tertiary": "beso"})

    def test_physical_realizations_of_one_alternative_get_distinct_keys(self):
        compiled = compile_telescope(parse_graph(jk15()))
        dark = [r for r in compiled.routes if r.function == "dark"]
        self.assertEqual(sorted(r.realization for r in dark), [0, 1, 2])  # dome closed / cover closed / M3 away
        keys = {(r.detector, r.function, r.alternative, r.realization) for r in compiled.routes}
        self.assertEqual(len(keys), len(compiled.routes))

    def test_conflicts_only_across_detectors_with_reasons(self):
        compiled = compile_telescope(parse_graph(with_beso_paths()))
        self.assertTrue(compiled.conflicts)
        for c in compiled.conflicts:
            self.assertNotEqual(c.a.detector, c.b.detector)
        m3 = [c for c in compiled.conflicts if c.selector == "tertiary" and c.a.function == "object" and c.b.function == "object"]
        self.assertEqual(len(m3), 1)
        self.assertEqual((m3[0].a.realization, m3[0].b.realization), (0, 0))
        self.assertEqual((m3[0].a_requires, m3[0].b_requires), ("andor", "beso"))
        self.assertEqual(m3[0].reason, "camera.object needs tertiary=andor, guider_beso.object needs tertiary=beso")
        # camera's dark-via-M3-away route and guider_beso's object route agree on tertiary=beso: no conflict
        agreeing = [c for c in compiled.conflicts if c.a.function == "dark" and c.b.function == "object" and c.selector == "tertiary" and c.a_requires == "beso"]
        self.assertEqual(agreeing, [])

    def test_splitter_branches_never_contend(self):
        comps = jk15(guider={"kind": "camera", "optics": {"from": {"pickoff": "guide"}}, "paths": {"object": "sky.science"}})
        compiled = compile_telescope(parse_graph(comps))
        self.assertEqual([c for c in compiled.conflicts if {c.a.detector, c.b.detector} == {"camera", "guider"} and c.a.function == "object" and c.b.function == "object"], [])

    def test_generated_routes_are_verified_against_the_graph(self):
        g = parse_graph(jk15())
        bogus = Route(detector="camera", function="object", alternative=0, see="sky.science", positions={"tertiary": "beso", "covercalibrator": "open", "dome": "open"})
        with self.assertRaises(CompileError) as cm:
            _verify(g, bogus, "sky")
        self.assertIn("dark@tertiary", str(cm.exception))

    def test_verification_requires_the_route_terminal(self):
        g = parse_graph(jk15())
        cover_lamp = Route(detector="camera", function="domeflat", alternative=0, see="lamp",
                           positions={"tertiary": "andor", "covercalibrator": "close", "covercalibrator.calibrator": "on"})
        _verify(g, cover_lamp, "covercalibrator")
        with self.assertRaises(CompileError) as cm:
            _verify(g, cover_lamp, "flatscreen")  # right class, wrong provenance
        self.assertIn("'lamp'@flatscreen but sees lamp@covercalibrator", str(cm.exception))

    def test_a_lamp_proven_off_is_a_dark_route_of_its_own(self):
        g = parse_graph(BESO)
        lamp_dark = [r for r in enumerate_routes(g, "beso") if r.terminal == "thar_lamp"]
        self.assertEqual(len(lamp_dark), 1)
        self.assertEqual(lamp_dark[0].classes, frozenset({"lamp", "dark"}))
        dark_routes = [r for r in compile_telescope(g).routes if (r.detector, r.function) == ("beso", "dark")]
        self.assertTrue(any(r.positions.get("m5") == "thar" and r.positions.get("m4") == "calib" for r in dark_routes))  # verified with the lamp off

    def test_routes_to_stateful_sources_verify_with_the_source_precondition(self):
        compiled = compile_telescope(parse_graph(BESO))  # arc and flat terminate at switchable lamps, object at the sky
        self.assertEqual({(r.function, r.see) for r in compiled.routes if r.detector == "beso"} >= {("arc", "lamp"), ("flat", "lamp"), ("object", "sky.science")}, True)
        arc = next(r for r in compiled.routes if (r.detector, r.function) == ("beso", "arc"))
        self.assertNotIn("thar_lamp", arc.positions)  # the lamp's power is not a route position: the sequence lights it

    def test_no_selectors_compiles_to_routes_without_positions(self):
        compiled = compile_telescope(parse_graph(TMMT))
        self.assertEqual(compiled.selectors, [])
        self.assertTrue(all(r.positions == {} for r in compiled.routes))
        self.assertEqual(compiled.conflicts, [])

    def test_compile_observatory_is_hashed_and_round_trips(self):
        telescopes = {"jk15": with_beso_paths(), "tmmt": TMMT}
        compiled = compile_observatory(telescopes, generator="ocabox-common test")
        self.assertIsInstance(compiled, OpticsCompiled)
        self.assertEqual(set(compiled.telescopes), {"jk15", "tmmt"})
        self.assertTrue(compiled.generated_from.startswith("sha256:"))
        self.assertEqual(compiled.generated_from, authored_hash(telescopes))
        self.assertEqual(OpticsCompiled.model_validate_json(compiled.model_dump_json(by_alias=True)), compiled)

    def test_authored_hash_sees_a_reordered_vocabulary_as_a_change(self):
        base = jk15()
        swapped = jk15(tertiary={**base["tertiary"], "positions": dict(reversed(list(base["tertiary"]["positions"].items())))})
        self.assertEqual(authored_hash({"jk15": jk15()}), authored_hash({"jk15": base}))
        self.assertNotEqual(authored_hash({"jk15": base}), authored_hash({"jk15": swapped}))  # order is semantic: the tie-break changes

    def test_hash_tracks_the_authored_input_only(self):
        a = authored_hash({"jk15": jk15()})
        self.assertEqual(a, authored_hash({"jk15": jk15()}))
        changed = jk15(tertiary={**jk15()["tertiary"], "positions": {"beso": {"port": 1}, "andor": {"port": 2}}})
        self.assertNotEqual(a, authored_hash({"jk15": changed}))
        self.assertNotEqual(a, authored_hash({"zb08": jk15()}))


if __name__ == "__main__":
    unittest.main()
