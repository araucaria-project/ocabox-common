"""Tests for the Staleness Contract's truth axis in ConditionalCycleQuery.

Phase 1 (ocabox-common#9 of epic ocabox-common#8): a declared
``ValuePolicy`` is serialized into cyclic-query ``request_data`` and, for
``NONE``, the client library synthesizes a rich ``Value(None)`` once
silence (router timeouts, retried server errors) outlives the
subscription's T2 (``time_of_data_max_age``, default ``2 * T1``).
Undeclared policies must be
bit-for-bit inert — that is the deployment-safety property that lets
1.3.0 ship before ocabox-server 2.5.2 reaches production.
"""

import asyncio
import unittest
from typing import List

from obcom.comunication.comunication_error import CommunicationTimeoutError
from obcom.comunication.cycle_query import ConditionalCycleQuery
from obcom.comunication.error_policy import (
    Backoff,
    Budget,
    ErrorPolicy,
    SeverityAction,
    SeverityRule,
    ValuePolicy,
)
from obcom.data_colection.address import Address
from obcom.data_colection.response_error import ResponseError
from obcom.data_colection.value import Value
from obcom.data_colection.value_call import ValueRequest, ValueResponse


def make_ok_response(addr: str = 'test.subject', v=42, ts: float = 0.0) -> ValueResponse:
    return ValueResponse(address=Address(addr),
                         value=Value(v=v, ts=ts, tags={'from_cf': True}),
                         status=True, error=None)


def make_error_response(addr: str = 'test.subject', code: int = 2003,
                        severity: str = ResponseError.SEVERITY_NORMAL) -> ValueResponse:
    return ValueResponse(address=Address(addr), value=None, status=False,
                         error=ResponseError(code=code, message='Synthetic error',
                                             severity=severity, component_name='test'))


class ScriptedSolver:
    """Serves scripted batches; an Exception entry is raised instead.

    The last entry repeats forever. Every ``send_request`` call records
    the (already-copied) requests it was given, so tests can assert on
    the request_data actually put on the wire.
    """

    def __init__(self, script: List):
        self._script = script
        self._calls = 0
        self.seen_requests: List[List[ValueRequest]] = []

    async def send_request(self, requests, timeout=None, no_wait=False):
        idx = min(self._calls, len(self._script) - 1)
        self._calls += 1
        self.seen_requests.append(list(requests))
        await asyncio.sleep(0)
        entry = self._script[idx]
        if isinstance(entry, BaseException):
            raise entry
        return list(entry)


def make_request(addr: str = 'test.subject', tolerance: float = 1.0) -> ValueRequest:
    return ValueRequest(address=Address(addr), time_of_data_tolerance=tolerance)


NONE_POLICY = ErrorPolicy.SERVICE.with_overrides(
    normal=SeverityRule(action=SeverityAction.RETRY, backoff=Backoff.immediate()),
    value_policy=ValuePolicy.NONE,
)


async def _drive(cq: ConditionalCycleQuery, calls: list, target: int, timeout: float = 1.0):
    cq.start()
    deadline = asyncio.get_event_loop().time() + timeout
    while len(calls) < target and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.005)
    cq.stop()


class TestValuePolicyWire(unittest.IsolatedAsyncioTestCase):
    """Serialization of the value_policy fragment into request_data."""

    async def test_declared_policy_serializes_fragment(self):
        crs = ScriptedSolver([[make_ok_response()]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []
        cq.add_callback_async_method(lambda r: calls.append(r) or asyncio.sleep(0))
        await _drive(cq, calls, target=1)
        await cq.stop_and_wait()
        self.assertTrue(crs.seen_requests)
        for r in crs.seen_requests[0]:
            self.assertEqual(r.request_data.get('value_policy'), 'none')

    async def test_undeclared_policy_sends_no_fragment(self):
        """SERVICE/INTERACTIVE stay inert — safe against pre-2.5.2 servers."""
        crs = ScriptedSolver([[make_ok_response()]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                   delay=0.01, error_policy=ErrorPolicy.SERVICE)
        calls = []
        cq.add_callback_async_method(lambda r: calls.append(r) or asyncio.sleep(0))
        await _drive(cq, calls, target=1)
        await cq.stop_and_wait()
        self.assertTrue(crs.seen_requests)
        for batch in crs.seen_requests:
            for r in batch:
                self.assertNotIn('value_policy', r.request_data)


class TestMaxAgeWire(unittest.IsolatedAsyncioTestCase):
    """T2 (`time_of_data_max_age`) rides as a first-class ValueRequest field."""

    async def test_declared_policy_defaults_max_age_to_twice_tolerance(self):
        crs = ScriptedSolver([[make_ok_response()]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.5)],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []
        cq.add_callback_async_method(lambda r: calls.append(r) or asyncio.sleep(0))
        await _drive(cq, calls, target=1)
        await cq.stop_and_wait()
        self.assertEqual(crs.seen_requests[0][0].time_of_data_max_age, 1.0)

    async def test_explicit_max_age_is_kept_and_clamped_to_tolerance(self):
        r = ValueRequest(address=Address('test.subject'), time_of_data_tolerance=1.0,
                         time_of_data_max_age=5.0)
        self.assertEqual(r.time_of_data_max_age, 5.0)
        clamped = ValueRequest(address=Address('test.subject'), time_of_data_tolerance=2.0,
                               time_of_data_max_age=0.5)
        self.assertEqual(clamped.time_of_data_max_age, 2.0)

    async def test_undeclared_request_is_wire_silent(self):
        """No policy → serialized dict is bit-for-bit pre-1.3.0: no T2 key."""
        r = ValueRequest(address=Address('test.subject'), time_of_data_tolerance=1.0)
        self.assertNotIn('time_of_data_max_age', r.to_dict())
        declared = ValueRequest(address=Address('test.subject'), time_of_data_tolerance=1.0,
                                time_of_data_max_age=5.0)
        self.assertEqual(declared.to_dict()['time_of_data_max_age'], 5.0)

    async def test_positional_constructor_compatibility(self):
        """T2 is declared last: pre-1.3.0 positional calls keep their meaning."""
        import time as _time
        deadline = _time.time() + 30
        r = ValueRequest('test.subject', _time.time(), 5.0, deadline)
        self.assertEqual(r.request_timeout, deadline)
        self.assertIsNone(r.time_of_data_max_age)

    async def test_nan_max_age_rejected(self):
        with self.assertRaises(ValueError):
            ValueRequest(address=Address('test.subject'), time_of_data_tolerance=1.0,
                         time_of_data_max_age=float('nan'))

    async def test_with_overrides_none_clears_the_axis(self):
        cleared = ErrorPolicy.DISPLAY.with_overrides(value_policy=None)
        self.assertIsNone(cleared.value_policy)
        # and not passing it keeps the preset's declaration
        kept = ErrorPolicy.DISPLAY.with_overrides(
            normal=SeverityRule(action=SeverityAction.RETRY, backoff=Backoff.immediate()))
        self.assertIs(kept.value_policy, ValuePolicy.NONE)

    async def test_notify_backoff_does_not_spam_callbacks(self):
        """The event must not stay set through a NOTIFY backoff — one callback
        per error batch, not a tight re-delivery loop."""
        policy = ErrorPolicy.SERVICE.with_overrides(
            normal=SeverityRule(action=SeverityAction.NOTIFY, backoff=Backoff.fixed(0.2)),
            value_policy=ValuePolicy.NONE,
        )
        crs = ScriptedSolver([[make_error_response(code=2003)]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=5.0)],
                                   delay=0.01, error_policy=policy)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        await asyncio.sleep(0.5)  # ~2-3 error batches at 0.2s backoff
        cq.stop()
        await cq.stop_and_wait()
        self.assertLessEqual(len(calls), 4, f'callback spam: {len(calls)} deliveries in 0.5s')
        self.assertGreaterEqual(len(calls), 1)

    async def test_synthesis_wakes_mid_backoff(self):
        """T2 expiring inside a long retry backoff must not delay the None
        until the next attempt — the sleep wakes at the deadline."""
        policy = ErrorPolicy.SERVICE.with_overrides(
            normal=SeverityRule(action=SeverityAction.RETRY, backoff=Backoff.fixed(1.5)),
            value_policy=ValuePolicy.NONE,
        )
        request = make_request(tolerance=0.05)
        request.time_of_data_max_age = 0.15
        crs = ScriptedSolver([[make_ok_response(v=1)], [make_error_response(code=2003)]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[request],
                                   delay=0.01, error_policy=policy)
        calls, t0 = [], asyncio.get_event_loop().time()
        stamps = []

        async def on_msg(resp):
            calls.append(list(resp))
            stamps.append(asyncio.get_event_loop().time() - t0)

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=2, timeout=1.0)
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 2)
        self.assertIsNone(calls[1][0].value.v)
        self.assertLess(stamps[1], 0.6,
                        f'stale-None at +{stamps[1]:.2f}s — waited out the 1.5s backoff instead of waking at T2')

    async def test_stale_none_supersedes_notify_error_past_t2(self):
        """When a NOTIFY error arrives past T2, the consumer gets ONE coherent
        delivery — the rich None (error code in tags) — not an error batch
        racing the synthesis for _last_response."""
        policy = ErrorPolicy.SERVICE.with_overrides(
            normal=SeverityRule(action=SeverityAction.NOTIFY, backoff=Backoff.immediate()),
            value_policy=ValuePolicy.NONE,
        )
        request = make_request(tolerance=0.05)
        request.time_of_data_max_age = 0.1
        crs = ScriptedSolver([[make_ok_response(v=1)], [make_error_response(code=2003)]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[request],
                                   delay=0.01, error_policy=policy)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        deadline = asyncio.get_event_loop().time() + 1.5
        while asyncio.get_event_loop().time() < deadline:
            if any(c[0].status and c[0].value and c[0].value.v is None for c in calls):
                break
            await asyncio.sleep(0.01)
        cq.stop()
        await cq.stop_and_wait()
        nones = [c for c in calls if c[0].status and c[0].value and c[0].value.v is None]
        self.assertEqual(len(nones), 1, 'exactly one stale-None per episode')
        self.assertEqual(nones[0][0].value.tags['reason'], 2003,
                         'the superseding None must carry the error code as reason')

    async def test_undeclared_policy_leaves_max_age_unset(self):
        crs = ScriptedSolver([[make_ok_response()]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                   delay=0.01, error_policy=ErrorPolicy.SERVICE)
        calls = []
        cq.add_callback_async_method(lambda r: calls.append(r) or asyncio.sleep(0))
        await _drive(cq, calls, target=1)
        await cq.stop_and_wait()
        self.assertIsNone(crs.seen_requests[0][0].time_of_data_max_age)

    async def test_synthesis_waits_for_max_age_not_tolerance(self):
        """Between T1 and T2 the last value is still the truth — no None yet."""
        script = [[make_ok_response(v=6)], CommunicationTimeoutError(message='x')]
        crs = ScriptedSolver(script)
        request = make_request(tolerance=0.05)
        request.time_of_data_max_age = 0.6
        cq = ConditionalCycleQuery(crs=crs, list_request=[request],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        await asyncio.sleep(0.3)  # well past T1, well before T2
        self.assertEqual(len(calls), 1, 'no stale-None inside the (T1, T2] window')
        deadline = asyncio.get_event_loop().time() + 2.0
        while len(calls) < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
        cq.stop()
        await cq.stop_and_wait()
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[1][0].value.v)


class HangingSolver:
    """Never answers; honors the absolute timeout like the real CRS does."""

    def __init__(self):
        self.calls = 0

    async def send_request(self, requests, timeout=None, no_wait=False):
        import time as _time
        self.calls += 1
        delay = (timeout - _time.time()) if timeout else 3600  # timeout is absolute wall clock
        await asyncio.sleep(delay if delay > 0 else 3600)
        from obcom.comunication.comunication_error import CommunicationTimeoutError as _CTE
        raise _CTE(message='router silent')


class TestLongPollWindowCap(unittest.IsolatedAsyncioTestCase):

    async def test_window_capped_at_t2_bounds_detection_latency(self):
        """A declared subscription must notice a dead router within ~T2, not
        within the default 30s request timeout (the client is blind while a
        long-poll is in flight)."""
        request = make_request(tolerance=0.6)
        request.time_of_data_max_age = 1.2
        crs = HangingSolver()
        cq = ConditionalCycleQuery(crs=crs, list_request=[request],
                                   delay=0.01, error_policy=NONE_POLICY)  # default 30s timeout
        self.assertLessEqual(cq._timeout, 1.2, 'long-poll window must be capped at T2')
        calls, t0 = [], asyncio.get_event_loop().time()
        stamps = []

        async def on_msg(resp):
            calls.append(list(resp))
            stamps.append(asyncio.get_event_loop().time() - t0)

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=1, timeout=4.0)
        await cq.stop_and_wait()
        self.assertEqual(len(calls) >= 1, True, 'no stale-None despite a silent router')
        self.assertIsNone(calls[0][0].value.v)
        self.assertLess(stamps[0], 2.5,
                        f'stale-None at +{stamps[0]:.2f}s — detection not bounded by T2')

    async def test_window_not_touched_for_undeclared(self):
        cq = ConditionalCycleQuery(crs=HangingSolver(), list_request=[make_request()],
                                   delay=0.01, error_policy=ErrorPolicy.SERVICE)
        self.assertEqual(cq._timeout, cq.DEFAULT_REQUEST_TIMEOUT)
        cq.stop()


class TestBatchSemantics(unittest.IsolatedAsyncioTestCase):

    async def test_mixed_t2_batch_synthesizes_at_the_tightest_bound(self):
        """A late None violates the tight member's truth bound; an early None
        for the loose member is merely conservative — trigger at min(T2)."""
        r1 = make_request(addr='test.a', tolerance=0.05)
        r1.time_of_data_max_age = 0.15
        r2 = make_request(addr='test.b', tolerance=0.05)
        r2.time_of_data_max_age = 5.0
        script = [[make_ok_response(addr='test.a', v=1), make_ok_response(addr='test.b', v=2)],
                  [make_error_response(addr='test.a'), make_error_response(addr='test.b')]]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[r1, r2],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls, t0, stamps = [], asyncio.get_event_loop().time(), []

        async def on_msg(resp):
            calls.append(list(resp))
            stamps.append(asyncio.get_event_loop().time() - t0)

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=2, timeout=2.0)
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(r.value.v is None for r in calls[1]), 'whole-batch None')
        self.assertLess(stamps[1], 1.0,
                        f'None at +{stamps[1]:.2f}s — waited for the LOOSE bound instead of the tight one')

    async def test_mixed_healthy_error_batch_does_not_mask_forever(self):
        """A repeated [healthy value, error] batch must not refresh the
        contact clock — the erroring member is owed its None at T2."""
        script = [[make_ok_response(addr='test.a', v=1), make_ok_response(addr='test.b', v=2)],
                  [make_ok_response(addr='test.a', v=1), make_error_response(addr='test.b')]]
        r1 = make_request(addr='test.a', tolerance=0.05)
        r2 = make_request(addr='test.b', tolerance=0.05)
        for r in (r1, r2):
            r.time_of_data_max_age = 0.15
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[r1, r2],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=2, timeout=2.0)
        await cq.stop_and_wait()
        nones = [c for c in calls if any(r.value is not None and r.value.v is None for r in c)]
        self.assertEqual(len(nones), 1,
                         'the erroring member must produce exactly one whole-batch stale-None')


class TestRound5Regressions(unittest.IsolatedAsyncioTestCase):

    async def test_catch_all_exception_path_synthesizes_at_t2(self):
        """A persistent unrecognized exception (retry-forever policy) must not
        leave the consumer showing its old value past T2 — the catch-all
        retry sleep wakes at the deadline and synthesizes."""
        class BrokenSolver:
            async def send_request(self, requests, timeout=None, no_wait=False):
                await asyncio.sleep(0)
                raise RuntimeError('internal blow-up')

        request = make_request(tolerance=0.05)
        request.time_of_data_max_age = 0.15
        cq = ConditionalCycleQuery(crs=BrokenSolver(), list_request=[request],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls, t0, stamps = [], asyncio.get_event_loop().time(), []

        async def on_msg(resp):
            calls.append(list(resp))
            stamps.append(asyncio.get_event_loop().time() - t0)

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=1, timeout=2.0)
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 1, 'no stale-None despite persistent internal failure')
        self.assertIsNone(calls[0][0].value.v)
        self.assertEqual(calls[0][0].value.tags['reason'], 'RuntimeError')
        self.assertLess(stamps[0], 1.0, f'None at +{stamps[0]:.2f}s — waited out the 60s catch-all delay')

    async def test_server_witnessed_none_value_counts_as_contact(self):
        """A legal Value(v=None) WITHOUT a reason tag is a healthy answer —
        it must feed the contact clock and never trigger a duplicate
        client-synthesized None."""
        bare_none = ValueResponse(address=Address('test.subject'),
                                  value=Value(v=None, ts=100.0, tags={'from_cf': True}),
                                  status=True, error=None)
        request = make_request(tolerance=0.05)
        request.time_of_data_max_age = 0.15
        crs = ScriptedSolver([[make_ok_response(v=1)], [bare_none]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[request],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        await asyncio.sleep(0.5)  # several T2 windows
        cq.stop()
        await cq.stop_and_wait()
        synthesized = [c for c in calls
                       if c[0].value is not None and c[0].value.v is None
                       and 'reason' in c[0].value.tags]
        self.assertEqual(synthesized, [], 'duplicate synthesized None on top of a server value-None')

    async def test_renewal_first_mixed_batch_still_synthesizes(self):
        """[4004, error] ordering must not refresh the contact clock — the
        erroring member is owed its stale-None at T2."""
        r1 = make_request(addr='test.a', tolerance=0.05)
        r2 = make_request(addr='test.b', tolerance=0.05)
        for r in (r1, r2):
            r.time_of_data_max_age = 0.15
        script = [[make_ok_response(addr='test.a', v=1), make_ok_response(addr='test.b', v=2)],
                  [make_error_response(addr='test.a', code=4004,
                                       severity=ResponseError.SEVERITY_TEMPORARY),
                   make_error_response(addr='test.b')]]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[r1, r2],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls, t0, stamps = [], asyncio.get_event_loop().time(), []

        async def on_msg(resp):
            calls.append(list(resp))
            stamps.append(asyncio.get_event_loop().time() - t0)

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=2, timeout=2.0)
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(r.value is not None and r.value.v is None for r in calls[1]))
        self.assertEqual(calls[1][0].value.tags['reason'], 2003,
                         'the error AFTER the 4004 must supply the synthesis reason')
        self.assertLess(stamps[1], 1.0,
                        f'None at +{stamps[1]:.2f}s — renewal-first ordering postponed synthesis')

    async def test_critical_behind_a_normal_error_stops_the_subscription(self):
        """[NORMAL, CRITICAL] batches: the CRITICAL member must be dispatched
        (STOP), not shadowed by the earlier NORMAL member's RETRY."""
        r1 = make_request(addr='test.a')
        r2 = make_request(addr='test.b')
        script = [[make_error_response(addr='test.a', code=2003,
                                       severity=ResponseError.SEVERITY_NORMAL),
                   make_error_response(addr='test.b', code=3002,
                                       severity=ResponseError.SEVERITY_CRITICAL)]]
        cq = ConditionalCycleQuery(crs=ScriptedSolver(script), list_request=[r1, r2],
                                   delay=0.01, error_policy=ErrorPolicy.SERVICE)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        deadline = asyncio.get_event_loop().time() + 1.0
        while not (cq._task is not None and cq._task.done()) \
                and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
        stopped = cq._task is not None and cq._task.done()  # observed BEFORE any cleanup stop
        await cq.stop_and_wait()
        self.assertTrue(stopped, 'CRITICAL must stop the subscription even behind a NORMAL error')

    async def test_same_severity_members_charge_the_budget_once_per_batch(self):
        """Retry state is keyed by severity: a two-address NORMAL batch must
        advance attempts once per poll, not once per member."""
        policy = ErrorPolicy.INTERACTIVE.with_overrides(
            normal=SeverityRule(action=SeverityAction.RETRY, backoff=Backoff.immediate(),
                                budget=Budget(max_attempts=4)))
        script = [[make_error_response(addr='test.a'), make_error_response(addr='test.b')],
                  [make_error_response(addr='test.a'), make_error_response(addr='test.b')],
                  [make_ok_response(addr='test.a', v=1), make_ok_response(addr='test.b', v=2)]]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs,
                                   list_request=[make_request(addr='test.a'),
                                                 make_request(addr='test.b')],
                                   delay=0.01, error_policy=policy)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=1, timeout=1.5)
        await cq.stop_and_wait()
        # per-member counting would hit max_attempts=4 after 2 polls and STOP
        # before the success; per-poll counting survives to deliver it
        self.assertTrue(calls and calls[0][0].status,
                        'budget exhausted early — attempts charged per member, not per poll')

    async def test_missed_limit_follows_transport_identity_not_value_policy(self):
        """FAIL_FAST + NONE: the caller's transport budget (max_missed_msg)
        still stops the subscription — the truth axis only shapes what is
        delivered, never whether the transport gives up."""
        policy = ErrorPolicy.FAIL_FAST.with_overrides(value_policy=ValuePolicy.NONE)
        request = make_request(tolerance=0.05)
        request.time_of_data_max_age = 0.1
        crs = ScriptedSolver([CommunicationTimeoutError(message='router gone')])
        cq = ConditionalCycleQuery(crs=crs, list_request=[request],
                                   delay=0.01, error_policy=policy, max_missed_msg=2)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        deadline = asyncio.get_event_loop().time() + 2.0
        while not cq.is_stopped() and asyncio.get_event_loop().time() < deadline:
            if cq._task is not None and cq._task.done():
                break
            await asyncio.sleep(0.02)
        stopped = cq.is_stopped() or (cq._task is not None and cq._task.done())
        await cq.stop_and_wait()
        self.assertTrue(stopped, 'FAIL_FAST identity must honor max_missed_msg despite value_policy=NONE')

    async def test_critical_after_renewal_stops_the_subscription(self):
        """A CRITICAL member after a 4004 must be dispatched (STOP), not be
        swallowed by the renewal branch's early exit."""
        r1 = make_request(addr='test.a')
        r2 = make_request(addr='test.b')
        script = [[make_error_response(addr='test.a', code=4004,
                                       severity=ResponseError.SEVERITY_TEMPORARY),
                   make_error_response(addr='test.b', code=3002,
                                       severity=ResponseError.SEVERITY_CRITICAL)]]
        cq = ConditionalCycleQuery(crs=ScriptedSolver(script), list_request=[r1, r2],
                                   delay=0.01, error_policy=ErrorPolicy.SERVICE)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        deadline = asyncio.get_event_loop().time() + 1.0
        while not (cq._task is not None and cq._task.done()) \
                and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
        stopped = cq._task is not None and cq._task.done()  # observed BEFORE any cleanup stop
        await cq.stop_and_wait()
        self.assertTrue(stopped, 'CRITICAL must stop the subscription even behind a 4004')


class TestStaleSynthesis(unittest.IsolatedAsyncioTestCase):

    async def test_router_silence_beyond_tolerance_delivers_stale_none_once(self):
        script = [[make_ok_response(v=42)], CommunicationTimeoutError(message='no router')]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=NONE_POLICY, max_missed_msg=3)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        deadline = asyncio.get_event_loop().time() + 2.0
        while len(calls) < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.005)
        # Let further timeouts churn to prove the None is not repeated
        # and that the query survives them (missed counter retired).
        await asyncio.sleep(0.2)
        alive = not cq.is_stopped()
        cq.stop()
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0][0].value.v, 42)
        stale = calls[1][0]
        self.assertTrue(stale.status)
        self.assertIsNone(stale.value.v)
        self.assertEqual(stale.value.tags['reason'], 4002)
        self.assertEqual(stale.value.tags['last_good'], 42)
        self.assertEqual(stale.value.tags['last_good_ts'], 0.0)
        self.assertEqual(len(calls), 2, "stale-None must be delivered exactly once per episode")
        # Tolerance clock replaces the missed counter for opted-in queries.
        self.assertTrue(alive, "opted-in subscription must survive max_missed_msg timeouts")

    async def test_normal_error_retry_beyond_tolerance_delivers_stale_none(self):
        script = [[make_ok_response(v=7)], [make_error_response(code=2003)]]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=2, timeout=2.0)
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 2)
        stale = calls[1][0]
        self.assertIsNone(stale.value.v)
        self.assertEqual(stale.value.tags['reason'], 2003)
        self.assertEqual(stale.value.tags['last_good'], 7)

    async def test_recovery_resets_episode_and_allows_second_synthesis(self):
        err = make_error_response(code=2003)
        # Paced backoff so each error episode outlives the 0.05 s tolerance
        # before the script moves on.
        policy = ErrorPolicy.SERVICE.with_overrides(
            normal=SeverityRule(action=SeverityAction.RETRY, backoff=Backoff.fixed(0.01)),
            value_policy=ValuePolicy.NONE,
        )
        script = ([[make_ok_response(v=1)]] + [[err]] * 40
                  + [[make_ok_response(v=2, ts=1.0)]] + [[err]])
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=policy)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=4, timeout=3.0)
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 4)
        values = [c[0].value.v for c in calls[:4]]
        self.assertEqual(values, [1, None, 2, None])
        self.assertEqual(calls[3][0].value.tags['last_good'], 2)

    async def test_persistent_notify_error_stays_suppressed_after_stale_none(self):
        """DISPLAY-style (NOTIFY + NONE): once the episode's stale-None is
        delivered, later raw error batches must not replace it — errors stay
        suppressed (callback and _last_response alike) until a real value
        resets the episode."""
        policy = ErrorPolicy.SERVICE.with_overrides(
            normal=SeverityRule(action=SeverityAction.NOTIFY, backoff=Backoff.immediate()),
            value_policy=ValuePolicy.NONE,
        )
        script = [[make_ok_response(v=5)], [make_error_response(code=2003)]]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=policy)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        deadline = asyncio.get_event_loop().time() + 2.0
        while (not calls or calls[-1][0].value is None or calls[-1][0].value.v is not None) \
                and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.005)
        # Errors keep arriving past T2 — prove they stay suppressed.
        await asyncio.sleep(0.2)
        held_view = list(cq._last_response)
        cq.stop()
        await cq.stop_and_wait()
        nones = [c for c in calls if c[0].status and c[0].value is not None and c[0].value.v is None]
        self.assertEqual(len(nones), 1, 'stale-None must be delivered exactly once per episode')
        self.assertIs(calls[-1], nones[0],
                      'no raw error batch may follow the episode stale-None')
        self.assertEqual(nones[0][0].value.tags['last_good'], 5)
        self.assertTrue(held_view and held_view[0].status and held_view[0].value.v is None,
                        '_last_response must keep the stale view while errors persist')

    async def test_startup_outage_none_carries_reason_alone(self):
        """No value was ever seen: the synthesized None carries `reason` only —
        the last_good tags are documented-optional and must not appear as
        explicit Nones masquerading as a known last-good value."""
        crs = ScriptedSolver([CommunicationTimeoutError(message='no router')])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=NONE_POLICY, max_missed_msg=-1)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=1, timeout=2.0)
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 1)
        stale = calls[0][0]
        self.assertTrue(stale.status)
        self.assertIsNone(stale.value.v)
        self.assertEqual(stale.value.tags['reason'], 4002)
        self.assertNotIn('last_good', stale.value.tags)
        self.assertNotIn('last_good_ts', stale.value.tags)

    async def test_last_good_policy_does_not_synthesize(self):
        policy = ErrorPolicy.SERVICE.with_overrides(value_policy=ValuePolicy.LAST_GOOD)
        script = [[make_ok_response(v=9)], CommunicationTimeoutError(message='no router')]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=policy)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=2, timeout=0.5)
        await cq.stop_and_wait()
        # Fragment serialized, but the aging value is left in place.
        self.assertEqual(crs.seen_requests[0][0].request_data.get('value_policy'), 'last_good')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].value.v, 9)

    async def test_4004_renewals_keep_value_truthful(self):
        """Long-poll renewals are healthy heartbeats: a value stable for
        longer than the tolerance is NOT stale while renewals confirm it."""
        script = [[make_ok_response(v=5)],
                  [make_error_response(code=4004, severity=ResponseError.SEVERITY_TEMPORARY)]]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=2, timeout=0.5)
        alive = not cq.is_stopped()
        await cq.stop_and_wait()
        self.assertEqual(len(calls), 1, "no stale-None while 4004 renewals confirm freshness")
        self.assertTrue(alive)

    async def test_server_delivered_stale_none_is_acknowledged_not_duplicated(self):
        """A >=2.6 server's rich None counts as healthy contact, informs the
        consumer (no client-side duplicate), and its ts is echoed as
        time_of_known_change so the server will not re-deliver it."""
        server_none = ValueResponse(
            address=Address('test.subject'),
            value=Value(v=None, ts=50.0, tags={'reason': 4009, 'from_cf': True,
                                               'last_good': 42, 'last_good_ts': 1.0}),
            status=True, error=None)
        script = [[make_ok_response(v=42, ts=1.0)], [server_none],
                  CommunicationTimeoutError(message='router gone')]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        deadline = asyncio.get_event_loop().time() + 1.0
        while len(calls) < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.005)
        # timeouts churn well past the tolerance — no third (synthesized) None
        await asyncio.sleep(0.2)
        cq.stop()
        await cq.stop_and_wait()
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[1][0].value.v)
        self.assertEqual(calls[1][0].value.tags['reason'], 4009)
        # the server None was acknowledged as a witnessed change
        self.assertEqual(crs.seen_requests[-1][0].request_data.get('time_of_known_change'), 50.0)

    async def test_stale_none_clears_time_of_known_change(self):
        """A synthesized None is an answer the server never witnessed: the
        request drops its change bookkeeping so that the first successful
        contact redelivers the current value unconditionally."""
        script = [[make_ok_response(v=3, ts=123.0)], CommunicationTimeoutError(message='x')]
        crs = ScriptedSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request(tolerance=0.05)],
                                   delay=0.01, error_policy=NONE_POLICY)
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _drive(cq, calls, target=2, timeout=2.0)
        await asyncio.sleep(0.05)  # a few more request cycles after synthesis
        await cq.stop_and_wait()
        self.assertGreaterEqual(len(calls), 2)
        self.assertIsNone(calls[1][0].value.v)
        last_batch = crs.seen_requests[-1]
        self.assertNotIn('time_of_known_change', last_batch[0].request_data)
        self.assertNotIn('no_send_before', last_batch[0].request_data)


class TestPolicyShape(unittest.TestCase):

    def test_default_policies_are_undeclared(self):
        for preset in (ErrorPolicy.INTERACTIVE, ErrorPolicy.SERVICE, ErrorPolicy.FAIL_FAST):
            self.assertIsNone(preset.value_policy)

    def test_display_preset(self):
        p = ErrorPolicy.DISPLAY
        self.assertIs(p.value_policy, ValuePolicy.NONE)
        self.assertEqual(p.normal.action, SeverityAction.RETRY)
        self.assertEqual(p.critical.action, SeverityAction.NOTIFY)

    def test_with_overrides_accepts_value_policy_string(self):
        p = ErrorPolicy.SERVICE.with_overrides(value_policy='none')
        self.assertIs(p.value_policy, ValuePolicy.NONE)
        # rules untouched
        self.assertEqual(p.normal.action, ErrorPolicy.SERVICE.normal.action)


if __name__ == '__main__':
    unittest.main()
