"""T0 — "not more often than ``delay``" — paced by the cycle query on its own clock, counted from
the delivery. The echoed ``no_send_before = ts + delay`` anchored it on the device read: beside a
faster subscriber the value served was already old and the server's gate opened early (jk15 M3:
``delay=1`` answered every 0.51 s). The server cannot pace it instead: after a 4004 renewal the
echo is the old one, and a gate on the request's arrival would hold every renewed request
(ocabox-server#66, reviewed)."""
import asyncio
import time
import unittest

from obcom.comunication.cycle_query import ConditionalCycleQuery
from obcom.comunication.error_policy import ErrorPolicy, ValuePolicy
from obcom.data_colection.address import Address
from obcom.data_colection.response_error import ResponseError
from obcom.data_colection.value_call import ValueRequest
from test.comunication.test_cycle_query_value_policy import ScriptedSolver, make_error_response, make_ok_response, make_request

RENEWAL = [make_error_response(code=4004, severity=ResponseError.SEVERITY_TEMPORARY)]
DISPLAY = ErrorPolicy.DISPLAY.with_overrides(value_policy=ValuePolicy.NONE)


class TimedSolver(ScriptedSolver):
    """Records when every request went out."""

    def __init__(self, script):
        super().__init__(script)
        self.sent_at: list[float] = []

    async def send_request(self, requests, timeout=None, no_wait=False):
        self.sent_at.append(time.monotonic())
        return await super().send_request(requests, timeout=timeout, no_wait=no_wait)


async def run(cq: ConditionalCycleQuery, seconds: float) -> None:
    cq.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        cq.stop()
        await cq.stop_and_wait()


class TestPacing(unittest.IsolatedAsyncioTestCase):

    async def test_the_next_request_waits_delay_after_a_delivery(self):
        # values served "old" (ts far in the past): the echoed gate is already open, only pacing holds
        crs = TimedSolver([[make_ok_response(v=n, ts=1.0 + n)] for n in range(20)])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()], delay=0.2, max_missed_msg=-1)
        await run(cq, 0.75)
        gaps = [b - a for a, b in zip(crs.sent_at, crs.sent_at[1:])]
        self.assertGreaterEqual(len(gaps), 2)
        self.assertTrue(all(gap >= 0.19 for gap in gaps), gaps)
        self.assertLessEqual(len(crs.sent_at), 5)  # 0.75 s / 0.2 s, not one per scheduler turn

    async def test_a_renewal_asks_again_at_once(self):
        crs = TimedSolver([[make_ok_response(v=1, ts=1.0)], RENEWAL, RENEWAL, [make_ok_response(v=2, ts=2.0)]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()], delay=0.3, max_missed_msg=-1)
        await run(cq, 0.5)
        delivered_then_renewed = crs.sent_at[2] - crs.sent_at[1]  # after renewal #1: no pause
        self.assertGreaterEqual(crs.sent_at[1] - crs.sent_at[0], 0.29)  # after the delivery: paced
        self.assertLess(delivered_then_renewed, 0.1)
        self.assertLess(crs.sent_at[3] - crs.sent_at[2], 0.1)

    async def test_the_pause_fits_the_long_poll_window(self):
        request = ValueRequest(address=Address('test.subject'), time_of_data_tolerance=0.1, time_of_data_max_age=1.4)
        crs = TimedSolver([[make_ok_response(v=n, ts=1.0 + n)] for n in range(20)])
        cq = ConditionalCycleQuery(crs=crs, list_request=[request], delay=5.0, max_missed_msg=-1, error_policy=DISPLAY)
        self.assertAlmostEqual(cq._pace_for, 1.4 - ConditionalCycleQuery.WINDOW_FLOOR)  # window T2, less the floor
        await run(cq, 1.0)
        self.assertGreaterEqual(len(crs.sent_at), 3)  # paced at 0.4 s, not at delay (5 s)


class FreezerLike:
    """Answers like the freezer: the first request a value at once, then each long poll held and
    renewed (4004) at 0.8 of its window."""

    def __init__(self):
        self.calls = 0

    async def send_request(self, requests, timeout=None, no_wait=False):
        self.calls += 1
        if self.calls == 1:
            return [make_ok_response(v=1, ts=time.time())]
        await asyncio.sleep(0.8 * (timeout - time.time()))
        return list(RENEWAL)


class TestContactAfterAPause(unittest.IsolatedAsyncioTestCase):
    """The renewal after a paced delivery lands inside the truth bound: the first window after a
    pause counts from the delivery, so ``is_contact_fresh()`` never drops (ocabox-common#43 review)."""

    async def assertContactStaysFresh(self, t2: float, delay: float):
        request = ValueRequest(address=Address('test.subject'), time_of_data_tolerance=0.5, time_of_data_max_age=t2)
        cq = ConditionalCycleQuery(crs=FreezerLike(), list_request=[request], delay=delay, max_missed_msg=-1,
                                   error_policy=DISPLAY)
        cq.start()
        try:
            await asyncio.sleep(0.05)  # the first delivery
            lost = []
            deadline = time.monotonic() + 2 * t2
            while time.monotonic() < deadline:
                if not cq.is_contact_fresh():
                    lost.append(round(cq.last_healthy_contact_age, 2))
                await asyncio.sleep(0.05)
        finally:
            cq.stop()
            await cq.stop_and_wait()
        self.assertEqual(lost, [], f'lost contact (ages) with T2={t2}, delay={delay}')

    async def test_delay_equal_to_the_truth_bound(self):
        await self.assertContactStaysFresh(t2=1.5, delay=1.5)  # the default shape: delay 5 = T2 5

    async def test_delay_just_below_the_truth_bound(self):
        await self.assertContactStaysFresh(t2=2.0, delay=1.5)

if __name__ == "__main__":
    unittest.main()
