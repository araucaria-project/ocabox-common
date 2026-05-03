"""Integration tests for ConditionalCycleQuery's error_policy dispatch.

A stub :class:`StubRequestSolver` plays the part of the network — it
serves a scripted sequence of responses on each ``send_request`` call,
so we can drive the cycle-query through every action × severity matrix
without a running ocabox-server.

Counts: each test asserts on **callback fire count** and **stop /
keep-running** state. Wall-clock timing is bounded with explicit
asyncio.timeout to keep the suite quick.
"""

import asyncio
import unittest
from typing import List

from obcom.comunication.cycle_query import ConditionalCycleQuery
from obcom.comunication.error_policy import (
    Backoff,
    Budget,
    ErrorPolicy,
    LogPolicy,
    SeverityAction,
    SeverityRule,
)
from obcom.data_colection.address import Address
from obcom.data_colection.response_error import ResponseError
from obcom.data_colection.value import Value
from obcom.data_colection.value_call import ValueRequest, ValueResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ok_response(addr: str = 'test.subject', v=1) -> ValueResponse:
    """Status=True response with a value tagged from_cf."""
    return ValueResponse(
        address=Address(addr),
        value=Value(v=v, ts=0.0, tags={'from_cf': True}),
        status=True,
        error=None,
    )


def make_error_response(addr: str = 'test.subject', code: int = 2003,
                        severity: str = ResponseError.SEVERITY_NORMAL,
                        message: str = 'Synthetic error') -> ValueResponse:
    """Status=False response carrying a ResponseError of the given severity."""
    return ValueResponse(
        address=Address(addr),
        value=None,
        status=False,
        error=ResponseError(code=code, message=message, severity=severity, component_name='test'),
    )


class StubRequestSolver:
    """Plays back a scripted list of response batches on each request.

    Once the script is exhausted, repeats the last entry forever — that
    way a steady-state retry test doesn't hang waiting for more script.
    Each batch is a ``List[ValueResponse]`` (one per request address).
    """

    def __init__(self, script: List[List[ValueResponse]]):
        self._script = script
        self._calls = 0
        self.observed_call_count = 0

    async def send_request(self, requests, timeout=None, no_wait=False):
        idx = min(self._calls, len(self._script) - 1)
        self._calls += 1
        self.observed_call_count = self._calls
        # Mirror real solver: yield to loop so the test can interleave
        # other tasks predictably.
        await asyncio.sleep(0)
        return list(self._script[idx])


def make_request(addr: str = 'test.subject') -> ValueRequest:
    return ValueRequest(address=Address(addr), time_of_data_tolerance=1.0)


async def _run_cq_until(cq: ConditionalCycleQuery, *, callback_calls: list,
                        target_calls: int, timeout: float = 1.0):
    """Helper: drive a cq until the callback has fired N times or timeout.

    Stops the cycle-query synchronously the moment the target is hit, so
    that follow-up shutdown latency doesn't add spurious extra callbacks.
    """
    cq.start()
    deadline = asyncio.get_event_loop().time() + timeout
    while len(callback_calls) < target_calls:
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.005)
    # Cancel the producer task immediately; any callbacks already
    # in-flight will still fire, but the producer won't queue more.
    cq.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestErrorPolicyDispatch(unittest.IsolatedAsyncioTestCase):

    async def test_normal_with_retry_policy_does_not_kill_subscription(self):
        """SERVICE preset: NORMAL error → library retries silently."""
        # Script: error, error, error, success (then repeats success)
        script = [
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_ok_response(v=42)],
        ]
        crs = StubRequestSolver(script)
        policy = ErrorPolicy.SERVICE.with_overrides(
            normal=SeverityRule(action=SeverityAction.RETRY, backoff=Backoff.immediate())
        )
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                    delay=0.01, error_policy=policy)
        callback_calls = []

        async def on_msg(resp):
            callback_calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _run_cq_until(cq, callback_calls=callback_calls, target_calls=1)
        await cq.stop_and_wait()
        # Property under test: NO error callback fired before the
        # success. The three NORMAL errors were swallowed by the
        # library's RETRY action.
        self.assertGreaterEqual(len(callback_calls), 1)
        self.assertTrue(callback_calls[0][0].status,
                        "first callback should be the success — error responses must be suppressed by RETRY")
        # Library actually retried.
        self.assertGreaterEqual(crs.observed_call_count, 4)

    async def test_normal_with_stop_policy_kills_subscription(self):
        """INTERACTIVE preset (default): NORMAL → callback gets one final fire, query dies."""
        script = [
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            # never reached
            [make_ok_response()],
        ]
        crs = StubRequestSolver(script)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                    delay=0.01, error_policy=ErrorPolicy.INTERACTIVE)
        callback_calls = []

        async def on_msg(resp):
            callback_calls.append(resp)

        cq.add_callback_async_method(on_msg)
        await _run_cq_until(cq, callback_calls=callback_calls, target_calls=1)
        # Give the cq a moment to fully shut down.
        await asyncio.sleep(0.05)
        # Callback fires once with the error response.
        self.assertEqual(len(callback_calls), 1)
        self.assertFalse(callback_calls[0][0].status)
        # Query is now stopped.
        self.assertTrue(cq.is_stopped() or (cq._task is not None and cq._task.done()))

    async def test_notify_action_fires_callback_and_keeps_retrying(self):
        """NOTIFY: callback sees error AND library keeps subscription alive."""
        script = [
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_ok_response(v=99)],
        ]
        crs = StubRequestSolver(script)
        policy = ErrorPolicy.INTERACTIVE.with_overrides(
            normal=SeverityRule(action=SeverityAction.NOTIFY, backoff=Backoff.immediate())
        )
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                    delay=0.01, error_policy=policy)
        callback_calls = []

        async def on_msg(resp):
            callback_calls.append(list(resp))

        cq.add_callback_async_method(on_msg)
        await _run_cq_until(cq, callback_calls=callback_calls, target_calls=3)
        await cq.stop_and_wait()
        # Property under test: callback sees both errors (NOTIFY) AND
        # then the eventual success (subscription stayed alive).
        self.assertGreaterEqual(len(callback_calls), 3)
        self.assertFalse(callback_calls[0][0].status, "first callback should be the NORMAL error")
        self.assertFalse(callback_calls[1][0].status, "second callback should be the NORMAL error")
        self.assertTrue(callback_calls[2][0].status, "third callback should be the success")

    async def test_budget_exhaustion_promotes_retry_to_stop(self):
        """An exhausted Budget on a RETRY rule converts to STOP."""
        script = [[make_error_response(severity=ResponseError.SEVERITY_NORMAL)]] * 10
        crs = StubRequestSolver(script)
        policy = ErrorPolicy.INTERACTIVE.with_overrides(
            normal=SeverityRule(
                action=SeverityAction.RETRY,
                backoff=Backoff.immediate(),
                budget=Budget(max_attempts=3),
            ),
        )
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                    delay=0.01, error_policy=policy)
        callback_calls = []

        async def on_msg(resp):
            callback_calls.append(resp)

        cq.add_callback_async_method(on_msg)
        await _run_cq_until(cq, callback_calls=callback_calls, target_calls=1, timeout=2.0)
        await asyncio.sleep(0.05)
        # After 3 retries the budget is spent → STOP fires the final
        # error callback once. Library should be stopped now.
        self.assertEqual(len(callback_calls), 1)
        self.assertFalse(callback_calls[0][0].status)
        # Solver was called between 3 (budget hit on attempt 3) and a
        # few more (race with the task shutdown).
        self.assertGreaterEqual(crs.observed_call_count, 3)
        self.assertLessEqual(crs.observed_call_count, 6)

    async def test_severity_state_resets_on_success(self):
        """A successful response between errors clears the retry counter."""
        script = [
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_ok_response(v=1)],
            # Stream resumes; second cluster of errors should not be
            # affected by the previous attempts counter (max_attempts=3
            # would otherwise have killed the second cluster on attempt 2).
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_ok_response(v=2)],
        ]
        crs = StubRequestSolver(script)
        policy = ErrorPolicy.INTERACTIVE.with_overrides(
            normal=SeverityRule(
                action=SeverityAction.RETRY,
                backoff=Backoff.immediate(),
                budget=Budget(max_attempts=3),
            ),
        )
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                    delay=0.01, error_policy=policy)
        callback_calls = []

        async def on_msg(resp):
            callback_calls.append(resp)

        cq.add_callback_async_method(on_msg)
        await _run_cq_until(cq, callback_calls=callback_calls, target_calls=2, timeout=2.0)
        await cq.stop_and_wait()
        # The first two callbacks must both be successes (v=1 then v=2).
        # If the counter hadn't reset, the second cluster would hit
        # budget exhaustion on the second error and emit a STOP-final
        # error callback before v=2 — meaning the second callback would
        # have status=False.
        self.assertGreaterEqual(len(callback_calls), 2)
        self.assertTrue(callback_calls[0][0].status, "first callback must be a success (v=1)")
        self.assertTrue(callback_calls[1][0].status,
                        "second callback must be a success (v=2) — budget should have reset")


class TestLegacyIgnoreErrorsCompat(unittest.IsolatedAsyncioTestCase):
    """``ignore_errors=True`` still does what it used to (DeprecationWarning aside)."""

    async def test_ignore_errors_true_retries_normal(self):
        import warnings
        script = [
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_error_response(severity=ResponseError.SEVERITY_NORMAL)],
            [make_ok_response(v=7)],
        ]
        crs = StubRequestSolver(script)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()],
                                        delay=0.01, ignore_errors=True)
        callback_calls = []

        async def on_msg(resp):
            callback_calls.append(resp)

        cq.add_callback_async_method(on_msg)
        await _run_cq_until(cq, callback_calls=callback_calls, target_calls=1)
        await cq.stop_and_wait()
        # Property under test: legacy ignore_errors=True still suppresses
        # NORMAL errors silently and lets the eventual success fire.
        self.assertGreaterEqual(len(callback_calls), 1)
        self.assertTrue(callback_calls[0][0].status,
                        "ignore_errors=True must keep retrying NORMAL silently — no error callback expected first")


if __name__ == '__main__':
    unittest.main()
