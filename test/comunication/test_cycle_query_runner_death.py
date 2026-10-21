"""Tests for dead callback runner handling in cycle queries (ocabox-common#41).

When the callback runner task dies from an exception outside per-callback
containment, the query must stop loudly at once: is_stopped() becomes True,
runner_failure is set, waiters wake, and an ERROR line is logged.
"""
import asyncio
from typing import List
import unittest
from unittest.mock import AsyncMock

from obcom.comunication.comunication_error import CommunicationRuntimeError
from obcom.comunication.cycle_query import ConditionalCycleQuery
from test.comunication.test_cycle_query_delivery_race import (
    ScriptedSolver,
    make_ok_response,
    make_request,
)


class DelayedScriptedSolver(ScriptedSolver):
    """ScriptedSolver with an optional initial delay to ensure waiters can park."""

    def __init__(self, script: List, initial_delay: float = 0.05):
        super().__init__(script)
        self._initial_delay = initial_delay

    async def send_request(self, requests, timeout=None, no_wait=False):
        if self._initial_delay > 0:
            await asyncio.sleep(self._initial_delay)
        return await super().send_request(requests, timeout=timeout, no_wait=no_wait)


class TestCycleQueryRunnerDeath(unittest.IsolatedAsyncioTestCase):

    async def test_runner_killed_outside_containment_stops_query_loudly(self):
        crs = ScriptedSolver([[make_ok_response(v=1)], [make_ok_response(v=2)]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()], delay=0.01)
        calls = []
        cq.add_callback_method(lambda r: calls.append(list(r)))

        cq._get_response_since = AsyncMock(side_effect=RuntimeError('boom'))

        with self.assertLogs('cycle_query', level='ERROR') as cm:
            cq.start()
            await asyncio.sleep(0.1)

        log_output = '\n'.join(cm.output)
        self.assertIn('CALLBACK RUNNER DIED', log_output)
        self.assertIn('RuntimeError', log_output)
        self.assertTrue(cq.is_stopped())
        self.assertIsInstance(cq.runner_failure, RuntimeError)
        self.assertEqual(str(cq.runner_failure), 'boom')
        self.assertTrue(cq._callback_task.done())
        self.assertIs(cq._callback_task.exception(), cq.runner_failure)
        self.assertEqual(len(calls), 0)

        await cq.stop_and_wait()

    async def test_parked_get_response_waiter_is_released(self):
        crs = DelayedScriptedSolver([[make_ok_response(v=1)], [make_ok_response(v=2)]], initial_delay=0.05)
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()], delay=0.01)

        orig_get = cq._get_response_since

        async def fail_runner_only(seen_seq):
            if asyncio.current_task() == cq._callback_task:
                raise RuntimeError('boom')
            return await orig_get(seen_seq)

        cq._get_response_since = AsyncMock(side_effect=fail_runner_only)

        with self.assertLogs('cycle_query', level='ERROR'):
            waiter = asyncio.create_task(cq.get_response())
            cq.start()

            try:
                async with asyncio.timeout(0.2):
                    with self.assertRaises(CommunicationRuntimeError):
                        await waiter
            finally:
                await cq.stop_and_wait()

    async def test_raising_callback_is_still_contained(self):
        # Sync callback
        crs = ScriptedSolver([[make_ok_response(v=1)], [make_ok_response(v=2)]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()], delay=0.01)
        calls_sync = []
        first_sync = True

        def sync_cb(resp):
            nonlocal first_sync
            if first_sync:
                first_sync = False
                raise ValueError('sync callback bug')
            calls_sync.append(resp[0].value.v)

        cq.add_callback_method(sync_cb)
        with self.assertLogs('cycle_query', level='ERROR') as cm_sync:
            cq.start()
            for _ in range(20):
                if calls_sync:
                    break
                await asyncio.sleep(0.01)

            self.assertFalse(cq.is_stopped())
            self.assertIsNone(cq.runner_failure)
            self.assertEqual(calls_sync[0], 2)
            self.assertTrue(any('sync callback bug' in msg for msg in cm_sync.output))

            await cq.stop_and_wait()

        # Async callback
        crs_async = ScriptedSolver([[make_ok_response(v=1)], [make_ok_response(v=2)]])
        cq_async = ConditionalCycleQuery(crs=crs_async, list_request=[make_request()], delay=0.01)
        calls_async = []
        first_async = True

        async def async_cb(resp):
            nonlocal first_async
            if first_async:
                first_async = False
                raise ValueError('async callback bug')
            calls_async.append(resp[0].value.v)

        cq_async.add_callback_async_method(async_cb)
        with self.assertLogs('cycle_query', level='ERROR') as cm_async:
            cq_async.start()
            for _ in range(20):
                if calls_async:
                    break
                await asyncio.sleep(0.01)

            self.assertFalse(cq_async.is_stopped())
            self.assertIsNone(cq_async.runner_failure)
            self.assertEqual(calls_async[0], 2)
            self.assertTrue(any('async callback bug' in msg for msg in cm_async.output))

            await cq_async.stop_and_wait()

    async def test_plain_stop_is_silent(self):
        crs = ScriptedSolver([[make_ok_response(v=1)]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()], delay=0.01)
        calls = []
        cq.add_callback_method(lambda r: calls.append(list(r)))

        with self.assertNoLogs('cycle_query', level='ERROR'):
            cq.start()
            for _ in range(20):
                if len(calls) >= 1:
                    break
                await asyncio.sleep(0.01)
            await cq.stop_and_wait()

        self.assertIsNone(cq.runner_failure)
        self.assertTrue(cq.is_stopped())

    async def test_restart_clears_failure(self):
        crs = ScriptedSolver([[make_ok_response(v=1)], [make_ok_response(v=2)]])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()], delay=0.01)
        calls = []
        cq.add_callback_method(lambda r: calls.append(r[0].value.v))

        orig_get = cq._get_response_since
        cq._get_response_since = AsyncMock(side_effect=RuntimeError('boom'))

        with self.assertLogs('cycle_query', level='ERROR'):
            cq.start()
            await asyncio.sleep(0.1)

        self.assertTrue(cq.is_stopped())
        self.assertIsInstance(cq.runner_failure, RuntimeError)

        # Restore _get_response_since and restart
        cq._get_response_since = orig_get
        cq.start()

        self.assertIsNone(cq.runner_failure)
        self.assertFalse(cq.is_stopped())

        for _ in range(20):
            if len(calls) >= 1:
                break
            await asyncio.sleep(0.01)

        self.assertGreaterEqual(len(calls), 1)
        await cq.stop_and_wait()


if __name__ == '__main__':
    unittest.main()
