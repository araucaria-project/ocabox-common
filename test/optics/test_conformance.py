import json
import tempfile
import unittest
from pathlib import Path

from datamodels.optics import ConformanceSuite

from obcom.optics import ProvenState, check_result, parse_graph, sees
from obcom.optics.conformance import SCENARIOS, build_suite, main, render


class TestConformanceSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.suite = build_suite()

    def test_one_vector_per_scenario_with_unique_names(self):
        self.assertEqual(len(self.suite.vectors), len(SCENARIOS))
        self.assertEqual(len({v.name for v in self.suite.vectors}), len(SCENARIOS))

    def test_red_team_cases_are_covered(self):
        names = " | ".join(v.name for v in self.suite.vectors)
        for needle in ("oca-problems#107", "calibrator lamp on", "idle branch", "moving", "stale", "no selectors", "beso"):
            self.assertIn(needle, names)

    def test_round_trips_through_the_datamodels_schema(self):
        text = render(self.suite)
        again = ConformanceSuite.model_validate_json(text)
        self.assertEqual(again, self.suite)
        raw = json.loads(text)
        self.assertEqual(set(raw["vectors"][0]), {"name", "description", "components", "state", "environment", "expected_sees", "expected_checks"})
        self.assertIn("class", raw["vectors"][0]["expected_sees"]["camera"][0])  # wire name

    def test_every_vector_replays_on_the_solver(self):
        """What a foreign traversal must do: rebuild the graph and state from the vector alone."""
        for vector in self.suite.vectors:
            with self.subTest(vector=vector.name):
                graph = parse_graph(vector.components)
                state = ProvenState(selectors=vector.state, environment=vector.environment)
                for detector, expected in vector.expected_sees.items():
                    self.assertEqual(sees(graph, state, detector), frozenset(expected))
                for expected in vector.expected_checks:
                    self.assertEqual(check_result(graph, state, expected.detector, expected.function), expected)

    def test_the_107_vector_says_undefined(self):
        vector = next(v for v in self.suite.vectors if "#107" in v.name)
        self.assertEqual(vector.state["tertiary"].raw, 2)
        self.assertEqual([r.light_class for r in vector.expected_sees["camera"]], ["undefined"])
        self.assertEqual([r.terminal for r in vector.expected_sees["camera"]], ["tertiary"])

    def test_cli_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "optics_conformance.json"
            self.assertEqual(main([str(out)]), 0)
            self.assertEqual(ConformanceSuite.model_validate_json(out.read_text()), self.suite)


if __name__ == "__main__":
    unittest.main()
