"""Regression test for silent callback-runner death.

Pre-fix: a callback that raises an exception other than TypeError kills
the callback runner task without logging. The producer keeps polling but
no further callbacks fire — downstream consumers see stale data forever.

Post-fix: the exception is caught with logger.exception (so the journal
shows the traceback), and the callback runner continues processing
subsequent responses.
"""
import asyncio
import logging
import unittest

from obcom.comunication.cycle_query import ConditionalCycleQuery
from obcom.comunication.error_policy import ErrorPolicy
from obcom.data_colection.address import Address
from obcom.data_colection.value import Value
from obcom.data_colection.value_call import ValueRequest, ValueResponse


class _StubRequestSolver:
    """Returns a fresh successful response on every call."""
    def __init__(self):
        self.calls = 0

    async def send_request(self, requests, timeout=None, no_wait=False):
        self.calls += 1
        await asyncio.sleep(0)
        return [ValueResponse(
            address=Address('test.subject'),
            value=Value(v=self.calls, ts=float(self.calls), tags={'from_cf': True}),
            status=True, error=None,
        )]


def _make_request() -> ValueRequest:
    return ValueRequest(address=Address('test.subject'), time_of_data_tolerance=0.5)


class TestCallbackRunnerRobustness(unittest.IsolatedAsyncioTestCase):

    async def test_callback_runner_survives_index_error(self):
        """A callback raising IndexError (not TypeError) must NOT kill the
        callback runner. Subsequent responses must continue to fire."""
        crs = _StubRequestSolver()
        cq = ConditionalCycleQuery(
            crs=crs, list_request=[_make_request()], delay=0.05,
            error_policy=ErrorPolicy.SERVICE,
        )

        fired_values: list[int] = []
        raise_first = [True]  # mutable so the callback can flip it

        async def fragile_callback(resps):
            if raise_first[0]:
                raise_first[0] = False
                raise IndexError("Simulated unexpected exception (not TypeError)")
            fired_values.append(resps[0].value.v)

        cq.add_callback_async_method(fragile_callback)
        cq.start()
        try:
            # Wait long enough for at least 2 successful callback firings
            # AFTER the initial failed one.
            deadline = asyncio.get_event_loop().time() + 2.0
            while len(fired_values) < 2 and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.05)
        finally:
            cq.stop()
            await asyncio.sleep(0.1)

        self.assertGreaterEqual(
            len(fired_values), 2,
            f"Callback runner died after IndexError. Only {len(fired_values)} "
            f"callbacks fired: {fired_values}. Producer is still polling but "
            f"no callbacks are being delivered — this is the silent-death "
            f"pathology."
        )

    async def test_callback_runner_survives_assertion_error(self):
        """Same property for AssertionError — also outside the TypeError catch."""
        crs = _StubRequestSolver()
        cq = ConditionalCycleQuery(
            crs=crs, list_request=[_make_request()], delay=0.05,
            error_policy=ErrorPolicy.SERVICE,
        )

        fired_values: list[int] = []
        raise_first = [True]

        async def fragile_callback(resps):
            if raise_first[0]:
                raise_first[0] = False
                assert False, "Simulated assertion failure"
            fired_values.append(resps[0].value.v)

        cq.add_callback_async_method(fragile_callback)
        cq.start()
        try:
            deadline = asyncio.get_event_loop().time() + 2.0
            while len(fired_values) < 2 and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.05)
        finally:
            cq.stop()
            await asyncio.sleep(0.1)

        self.assertGreaterEqual(len(fired_values), 2)

    async def test_callback_exception_is_logged(self):
        """The exception that previously died silently must now log with
        traceback so operators can diagnose."""
        crs = _StubRequestSolver()
        cq = ConditionalCycleQuery(
            crs=crs, list_request=[_make_request()], delay=0.05,
            error_policy=ErrorPolicy.SERVICE,
        )

        async def fragile_callback(resps):
            raise IndexError("This must show up in the journal")

        cq.add_callback_async_method(fragile_callback)
        with self.assertLogs(level=logging.ERROR) as cm:
            cq.start()
            await asyncio.sleep(0.5)
            cq.stop()
            await asyncio.sleep(0.1)

        joined = "\n".join(cm.output)
        self.assertIn("IndexError", joined)
        self.assertIn("This must show up in the journal", joined)

    async def test_cancelled_error_still_propagates(self):
        """The fix MUST NOT widen catch to BaseException. Cancellation
        must still stop the callback runner cleanly when stop() is called."""
        crs = _StubRequestSolver()
        cq = ConditionalCycleQuery(
            crs=crs, list_request=[_make_request()], delay=0.05,
            error_policy=ErrorPolicy.SERVICE,
        )

        callback_count = [0]

        async def normal_callback(resps):
            callback_count[0] += 1

        cq.add_callback_async_method(normal_callback)
        cq.start()
        await asyncio.sleep(0.2)
        self.assertGreater(callback_count[0], 0,
                           "Sanity: at least one callback should fire before stop.")
        await cq.stop_and_wait()
        callbacks_at_stop = callback_count[0]
        await asyncio.sleep(0.3)  # let any zombie callback try to fire
        self.assertEqual(
            callback_count[0], callbacks_at_stop,
            "Cancellation must stop the callback runner — no further callbacks "
            "should fire after stop_and_wait()."
        )
