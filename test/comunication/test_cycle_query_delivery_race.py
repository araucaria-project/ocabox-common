"""Regression tests for the event-pulse delivery race (ocabox-common#18).

Pre-fix: ``_pulse_event()`` was ``set(); await asyncio.sleep(0); clear()``.
An ``asyncio.Event`` pulse like that is lossy for any consumer not already
parked in ``wait()`` during that one-yield window. The callback runner
(``_execute_callbacks`` -> ``get_response``) is not parked whenever it is
busy awaiting a user callback, or whenever it has not started/parked yet
when the very first delivery happens (extreme starvation case).

Because ``ConditionalCycleQuery`` deliveries are once-per-change (and the
stale-None is once-per-episode), a pulse lost to either race is not "one
skipped frame" — the batch is lost until the next change/episode.

Post-fix: delivery is tracked with a monotonically increasing sequence
number (``_delivery_seq``); ``get_response`` gates on that counter rather
than on the event's set/cleared edge, so it becomes loss-free for a
consumer that is merely busy or not yet parked, while still delivering
each change/episode exactly once.
"""
import asyncio
import unittest
from typing import List
from unittest.mock import patch

from obcom.comunication.comunication_error import CommunicationTimeoutError
from obcom.comunication.cycle_query import ConditionalCycleQuery
from obcom.comunication.error_policy import ErrorPolicy, ValuePolicy
from obcom.data_colection.address import Address
from obcom.data_colection.value import Value
from obcom.data_colection.value_call import ValueRequest, ValueResponse


def make_ok_response(addr: str = 'test.subject', v=42, ts: float = 0.0) -> ValueResponse:
    return ValueResponse(address=Address(addr),
                         value=Value(v=v, ts=ts, tags={'from_cf': True}),
                         status=True, error=None)


def make_request(addr: str = 'test.subject', tolerance: float = 1.0) -> ValueRequest:
    return ValueRequest(address=Address(addr), time_of_data_tolerance=tolerance)


class ScriptedSolver:
    """Serves scripted batches; an Exception entry is raised instead.

    The last entry repeats forever.
    """

    def __init__(self, script: List):
        self._script = script
        self._calls = 0

    async def send_request(self, requests, timeout=None, no_wait=False):
        idx = min(self._calls, len(self._script) - 1)
        self._calls += 1
        await asyncio.sleep(0)
        entry = self._script[idx]
        if isinstance(entry, BaseException):
            raise entry
        return list(entry)


class TestDeliveryRace(unittest.IsolatedAsyncioTestCase):

    async def test_value_change_mid_callback_is_not_lost(self):
        """Busy consumer: a slow async callback must not lose the next
        value change that arrives while it is still running.

        Red on pre-fix master: the second delivery's pulse (set/sleep(0)/
        clear) can complete while the runner is awaiting the first
        callback (not parked in ``_event.wait()``), so the runner's next
        ``get_response()`` call parks forever waiting for a THIRD delivery
        that never comes.
        """
        resp1 = [make_ok_response(v=1, ts=1.0)]
        resp2 = [make_ok_response(v=2, ts=2.0)]
        crs = ScriptedSolver([resp1, resp2, CommunicationTimeoutError(message='no more changes')])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=1.0)],
                                   delay=0.01, max_missed_msg=-1)
        calls = []
        callback_started = asyncio.Event()
        release_callback = asyncio.Event()

        async def on_msg(resp):
            if not calls:
                callback_started.set()
                await release_callback.wait()
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        try:
            await asyncio.wait_for(callback_started.wait(), timeout=2.0)

            # The runner is now busy inside the first callback invocation —
            # NOT parked in `_event.wait()`. Let the producer deliver the
            # second, distinct value while the runner stays busy. Waiting
            # for a 3rd request to start (rather than polling an internal
            # delivery counter) proves the 2nd response was already fully
            # processed, since that happens before the next request is sent.
            deadline = asyncio.get_event_loop().time() + 2.0
            while crs._calls < 3 and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.005)
            self.assertGreaterEqual(
                crs._calls, 3,
                "producer must have delivered the second value while the runner was busy")

            release_callback.set()
            deadline = asyncio.get_event_loop().time() + 2.0
            while len(calls) < 2 and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.005)
            final_seq = getattr(cq, '_delivery_seq', None)
        finally:
            cq.stop()
            await cq.stop_and_wait()

        self.assertEqual(len(calls), 2, "both deliveries must reach the callback, none lost")
        self.assertEqual(calls[0][0].value.v, 1)
        self.assertEqual(calls[1][0].value.v, 2)
        # No duplicate delivery: exactly one callback per change.
        self.assertEqual(final_seq, 2)

    async def test_stale_none_survives_runner_starting_after_delivery(self):
        """First-poll starvation: the callback-runner task has not been
        created yet (so it cannot possibly be parked in ``wait()``) when
        the producer synthesizes and delivers the stale-None.

        Red on pre-fix master: the pulse (set/sleep(0)/clear) can run to
        completion before the runner task even exists, clearing the event
        before anyone ever waited on it — the synthesized batch sits in
        ``_last_response`` forever, and the callback is never invoked.
        """
        policy = ErrorPolicy.INTERACTIVE.with_overrides(value_policy=ValuePolicy.NONE)
        crs = ScriptedSolver([CommunicationTimeoutError(message='starved loop')])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=policy, max_missed_msg=-1)
        cq._last_contact_ts = cq._last_contact_ts - 2.0
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)

        # Start ONLY the producer task (`_run`), not the callback runner
        # (`_run_callbacks`) — mirrors `start()` without its second half.
        with patch.object(cq, '_detect_local_starvation', return_value=(True, 0.8, 0.5)):
            cq._run()
            try:
                deadline = asyncio.get_event_loop().time() + 2.0
                while not cq._stale_delivered and asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(0.005)
                self.assertTrue(cq._stale_delivered,
                                "producer must have synthesized+delivered a stale-None")

                # NOW start the runner: this is the "not yet parked" race window.
                cq._run_callbacks()
                deadline = asyncio.get_event_loop().time() + 2.0
                while not calls and asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(0.005)
                final_seq = getattr(cq, '_delivery_seq', None)
            finally:
                cq.stop()
                await cq.stop_and_wait()

        self.assertEqual(len(calls), 1, "the stale-None must be delivered exactly once, not lost")
        self.assertIsNone(calls[0][0].value.v)
        self.assertEqual(calls[0][0].value.tags['reason'], 4010)
        # No duplicate delivery for the same episode.
        self.assertEqual(final_seq, 1)


if __name__ == '__main__':
    unittest.main()
