import asyncio
import unittest

from obcom.comunication.cycle_query import ConditionalCycleQuery
from obcom.comunication.error_policy import Backoff, Budget, ErrorPolicy, SeverityAction, SeverityRule
from obcom.data_colection.response_error import ResponseError
from test.comunication.test_cycle_query_error_policy import (
    StubRequestSolver,
    _run_cq_until,
    make_error_response,
    make_ok_response,
    make_request,
)


async def _wait_until_stopped(cq: ConditionalCycleQuery, timeout: float = 1.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if cq._task is not None and cq._task.done():
            return
        await asyncio.sleep(0.005)


class TestCycleQueryStop(unittest.IsolatedAsyncioTestCase):

    async def test_critical_delivers_error_once_then_stops(self):
        script = [
            [make_error_response(severity=ResponseError.SEVERITY_CRITICAL, code=3002)],
            [make_ok_response(v=11)],
        ]
        cq = ConditionalCycleQuery(
            crs=StubRequestSolver(script),
            list_request=[make_request()],
            delay=0.01,
            error_policy=ErrorPolicy.DISPLAY,
        )
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        cq.start()
        await _wait_until_stopped(cq)
        await asyncio.sleep(0.05)
        await cq.stop_and_wait()

        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][0].status)
        self.assertTrue(cq.is_stopped() or (cq._task is not None and cq._task.done()))

    async def test_stop_reason_exposed(self):
        code = 3002
        script = [[make_error_response(severity=ResponseError.SEVERITY_CRITICAL, code=code)]]
        cq = ConditionalCycleQuery(
            crs=StubRequestSolver(script),
            list_request=[make_request()],
            delay=0.01,
            error_policy=ErrorPolicy.DISPLAY,
        )
        cq.start()
        await _wait_until_stopped(cq)
        await cq.stop_and_wait()
        self.assertIsNotNone(cq.stop_reason)
        self.assertEqual(cq.stop_reason.code, code)
        self.assertEqual(cq.stop_reason.severity, ResponseError.SEVERITY_CRITICAL)

    async def test_stop_reason_none_after_plain_stop(self):
        script = [[make_ok_response(v=1)]]
        cq = ConditionalCycleQuery(
            crs=StubRequestSolver(script),
            list_request=[make_request()],
            delay=0.01,
            error_policy=ErrorPolicy.DISPLAY,
        )
        calls = []

        async def on_msg(resp):
            calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _run_cq_until(cq, callback_calls=calls, target_calls=1)
        await cq.stop_and_wait()
        self.assertIsNone(cq.stop_reason)

    async def test_budget_exhaustion_sets_stop_reason(self):
        script = [[make_error_response(severity=ResponseError.SEVERITY_NORMAL, code=2003)]] * 8
        policy = ErrorPolicy.INTERACTIVE.with_overrides(
            normal=SeverityRule(
                action=SeverityAction.RETRY,
                backoff=Backoff.immediate(),
                budget=Budget(max_attempts=2),
            ),
        )
        cq = ConditionalCycleQuery(
            crs=StubRequestSolver(script),
            list_request=[make_request()],
            delay=0.01,
            error_policy=policy,
        )
        cq.start()
        await _wait_until_stopped(cq, timeout=2.0)
        await cq.stop_and_wait()
        self.assertIsNotNone(cq.stop_reason)
        self.assertEqual(cq.stop_reason.code, 2003)
        self.assertEqual(cq.stop_reason.severity, ResponseError.SEVERITY_NORMAL)

    async def test_stop_logs_one_error_line(self):
        script = [[make_error_response(
            addr='scope.bad.address',
            severity=ResponseError.SEVERITY_CRITICAL,
            code=3002,
        )]]
        cq = ConditionalCycleQuery(
            crs=StubRequestSolver(script),
            list_request=[make_request(addr='scope.bad.address')],
            delay=0.01,
            error_policy=ErrorPolicy.DISPLAY,
        )
        with self.assertLogs('cycle_query', level='ERROR') as cm:
            cq.start()
            await _wait_until_stopped(cq)
            await cq.stop_and_wait()
        self.assertEqual(len(cm.records), 1)
        message = cm.records[0].getMessage()
        self.assertIn('scope.bad.address', message)
        self.assertIn('code=3002', message)
        self.assertIn('severity=CRITICAL', message)
        self.assertIn('subscription stopped', message)


if __name__ == '__main__':
    unittest.main()
