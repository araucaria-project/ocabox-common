"""The timing defaults (``obcom.comunication.timing``): one T1 for cache and wire, T0 defaults to T1,
``delay=0`` is no rate cap, the derived default always fits the long-poll window, T2's floor is
derived from the round-trip budget, and T1 is never inherited from T0."""
import asyncio
import unittest

from obcom.comunication import timing
from obcom.comunication.base_client_api import BaseClientAPI
from obcom.comunication.cycle_query import BaseCycleQuery, ConditionalCycleQuery, PeriodicCycleQuery
from obcom.comunication.error_policy import ErrorPolicy, ValuePolicy
from obcom.data_colection.address import Address
from obcom.data_colection.value_call import ValueRequest
from test.comunication.test_cycle_query_value_policy import ScriptedSolver, make_ok_response, make_request

DISPLAY = ErrorPolicy.DISPLAY.with_overrides(value_policy=ValuePolicy.NONE)


class StubClientAPI(BaseClientAPI):
    """Just enough of a client API to build requests and cycle queries."""

    def __init__(self):
        self.crs = ScriptedSolver([])

    @property
    def user(self):
        return None

    @property
    def _CRS(self):
        return self.crs

    async def server_is_alive(self, request_timeout=None): return True
    async def server_reload_nats_config(self, request_timeout=None): return True
    def get_cfg(self, name_cfg, default=None, use_default_settings=True): return default
    def get_cfg_deep(self, name_cfg, default=None, use_default_settings=True): return default


class TestConstants(unittest.TestCase):

    def test_floor_is_rtt_budget_over_margin(self):
        # a change of RTT_BUDGET or SERVER_MARGIN_FRACTION must be a conscious one: it moves every default T2
        self.assertEqual(timing.T2_FLOOR, timing.RTT_BUDGET / timing.SERVER_MARGIN_FRACTION)
        self.assertEqual(timing.T2_FLOOR, 5.0)
        self.assertEqual(ValueRequest.DEFAULT_MAX_AGE_FLOOR, timing.T2_FLOOR)

    def test_freezer_margin_leaves_credibility_headroom(self):
        # a legal renewal leaves the server at (1 − margin)·W and must count as credible (≥ CREDIBLE·W)
        self.assertGreater(1 - timing.SERVER_MARGIN_FRACTION, timing.RENEWAL_CREDIBLE_FRACTION)

    def test_one_t1_on_the_wire(self):
        self.assertEqual(ValueRequest.DEFAULT_TIME_OF_DATA_TOLERANCE, timing.DEFAULT_REFRESH_AFTER)
        self.assertEqual(ValueRequest(address=Address('a.b')).time_of_data_tolerance, timing.DEFAULT_REFRESH_AFTER)

    def test_windows(self):
        self.assertEqual(BaseCycleQuery.DEFAULT_REQUEST_TIMEOUT, timing.UNDECLARED_WINDOW)
        self.assertEqual(BaseCycleQuery.WINDOW_FLOOR, timing.WINDOW_FLOOR)


class TestDelayDefaults(unittest.IsolatedAsyncioTestCase):

    async def test_delay_defaults_to_the_tightest_tolerance(self):
        cq = ConditionalCycleQuery(crs=ScriptedSolver([]),
                                   list_request=[make_request(tolerance=2.0), make_request(tolerance=0.5)])
        self.assertEqual(cq._delay, 0.5)
        self.assertFalse(cq._delay_explicit)
        self.assertEqual(cq._pace_for, 0.5)

    async def test_delay_zero_means_no_cap(self):
        cq = ConditionalCycleQuery(crs=ScriptedSolver([]), list_request=[make_request()], delay=0)
        self.assertEqual(cq._delay, 0.0)
        self.assertEqual(cq._pace_for, 0.0)
        cq._delivered_mono = asyncio.get_running_loop().time()
        await asyncio.wait_for(cq._pace(), 0.05)  # returns at once: nothing to wait for

    async def test_negative_delay_is_rejected(self):
        with self.assertRaises(ValueError):
            ConditionalCycleQuery(crs=ScriptedSolver([]), list_request=[make_request()], delay=-1)

    async def test_derived_default_never_warns(self):
        # a NONE client on defaults: T1 0.5 → T2 5 → W 5 → the pause 0.5 fits; the library must not warn
        # about its own default (before: delay 5 against a 5 s window, one warning per subscription)
        with self.assertNoLogs('cycle_query', level='WARNING'):
            cq = ConditionalCycleQuery(crs=ScriptedSolver([]), list_request=[make_request(tolerance=0.5)],
                                       error_policy=DISPLAY)
        self.assertEqual((cq._delay, cq._timeout, cq._pace_for), (0.5, 5.0, 0.5))

    async def test_explicit_delay_that_does_not_fit_warns_and_is_clipped(self):
        with self.assertLogs('cycle_query', level='WARNING') as cm:
            cq = ConditionalCycleQuery(crs=ScriptedSolver([]), list_request=[make_request(tolerance=0.5)],
                                       delay=5.0, error_policy=DISPLAY)
        self.assertEqual(cq._pace_for, 5.0 - timing.WINDOW_FLOOR)
        self.assertTrue(any('does not fit' in line for line in cm.output))

    async def test_periodic_min_delay_is_clamped(self):
        with self.assertLogs('cycle_query', level='WARNING'):
            cq = PeriodicCycleQuery(crs=ScriptedSolver([]), list_request=[make_request()], delay=0.1)
        self.assertEqual(cq._delay, timing.PERIODIC_MIN_DELAY)


class TestClientApiInheritance(unittest.IsolatedAsyncioTestCase):

    async def test_tolerance_not_inherited_from_delay(self):
        api = StubClientAPI()
        cq = await api.subscribe('a.b', delay=7.0)
        request = cq._list_request[0]
        self.assertEqual(request.time_of_data_tolerance, timing.DEFAULT_REFRESH_AFTER)  # T1 is its own default
        self.assertEqual(cq._delay, 7.0)
        self.assertTrue(cq._delay_explicit)

    async def test_subscription_on_defaults(self):
        api = StubClientAPI()
        cq = await api.subscribe('a.b')
        request = cq._list_request[0]
        self.assertEqual(request.time_of_data_tolerance, timing.DEFAULT_REFRESH_AFTER)
        self.assertEqual(cq._delay, timing.DEFAULT_REFRESH_AFTER)
        self.assertEqual(cq._timeout, timing.UNDECLARED_WINDOW)


if __name__ == '__main__':
    unittest.main()
