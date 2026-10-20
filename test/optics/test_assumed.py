import unittest

from datamodels.optics import Active, Impossible, Settable, state_key

from obcom.optics import Declaration, ProvenState, check, parse_graph, published_state, resolve_axes, sees
from test.optics.fixtures import BESO, jk15, night


def selectors(graph, state: ProvenState) -> dict[str, dict]:
    """What the optics service publishes as ``self.selectors`` for ``state``."""
    out = {}
    for node in graph.nodes.values():
        for aspect, resolved in resolve_axes(graph, state, node).items():
            out[state_key(node.name, aspect)] = {"position": resolved.value, "origin": resolved.origin, "cause": resolved.cause}
    return out


class TestAssumed(unittest.TestCase):

    def setUp(self):
        self.g = parse_graph(jk15())

    def axis(self, state, key):
        component, _, aspect = key.partition(".")
        return resolve_axes(self.g, state, self.g.node(component))[aspect or None]

    def test_a_derived_axis_can_be_assumed(self):
        day = night().assuming({"sky": "day"})
        self.assertEqual(self.axis(day, "sky").value, "day")
        self.assertEqual(self.axis(day, "sky").origin, "assumed")
        self.assertIsInstance(check(self.g, day, "camera", "object"), Impossible)

    def test_an_assumption_wins_over_a_reading(self):
        state = night().assuming({"tertiary": "beso"})
        self.assertEqual(self.axis(state, "tertiary").value, "beso")
        self.assertEqual({r.terminal for r in sees(self.g, state, "camera")}, {"tertiary"})

    def test_an_assumed_unknown_or_unknown_symbol_is_undefined_and_says_so(self):
        self.assertEqual(self.axis(night().assuming({"tertiary": None}), "tertiary").cause, "assumed unknown")
        typo = self.axis(night().assuming({"tertiary": "bseo"}), "tertiary")
        self.assertIsNone(typo.value)
        self.assertEqual(typo.cause, "assumed 'bseo', not a known position")

    def test_the_moves_of_a_route_override_what_was_assumed(self):
        moved = night().assuming({"tertiary": "beso"}).with_positions({"tertiary": "andor"})
        self.assertEqual(self.axis(moved, "tertiary").value, "andor")

    def test_every_state_axis_with_its_vocabulary(self):
        axes = self.g.state_axes
        self.assertIn("sky", axes)
        self.assertIn("dome", axes)
        self.assertEqual(axes["covercalibrator.calibrator"], ("off", "on"))


class TestPublishedState(unittest.TestCase):
    """The solver over what the service publishes answers as the service does."""

    def assertAgrees(self, graph, state):
        again = published_state(selectors(graph, state))
        for detector in graph.detectors:
            self.assertEqual(sees(graph, again, detector), sees(graph, state, detector), detector)
            for function in graph.node(detector).paths or {}:
                self.assertEqual(check(graph, again, detector, function), check(graph, state, detector, function), f"{detector}.{function}")

    def test_a_night_of_imaging(self):
        self.assertAgrees(parse_graph(jk15()), night())

    def test_closed_covers_in_twilight(self):
        self.assertAgrees(parse_graph(jk15()), night(sun_alt_deg=-8.0, covercalibrator="close"))

    def test_an_unknown_dome_keeps_its_cause(self):
        g = parse_graph(jk15())
        state = ProvenState.build({"tertiary": "andor", "covercalibrator": "open", "covercalibrator.calibrator": "off"},
                                  sun_alt_deg=-30.0, causes={"dome": "shutter reports Error (ShutterState 4)"})
        self.assertAgrees(g, state)
        again = published_state(selectors(g, state))
        self.assertEqual(resolve_axes(g, again, g.node("dome"))[None].cause, "shutter reports Error (ShutterState 4)")

    def test_a_lamp_switched_on_by_hand_and_declared(self):
        g = parse_graph(BESO)
        state = ProvenState.build({"tertiary": "beso", "covercalibrator": "open", "covercalibrator.calibrator": "off",
                                   "m4": "calib", "m5": "thar", "white_lamp": "off"}, sun_alt_deg=-30.0, dome_shutter_open=True)
        state = ProvenState(selectors=state.selectors, environment=state.environment, declared={"thar_lamp": Declaration("on", "observer")})
        self.assertAgrees(g, state)
        self.assertIsInstance(check(g, published_state(selectors(g, state)), "beso", "arc"), Active)

    def test_a_what_if_on_top(self):
        g = parse_graph(jk15())
        what_if = published_state(selectors(g, night())).assuming({"covercalibrator": "close"})
        self.assertIsInstance(check(g, what_if, "camera", "object"), Settable)


if __name__ == "__main__":
    unittest.main()
