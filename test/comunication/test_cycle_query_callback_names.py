"""Any callable is a callback: the runner names it for its log, and a callback without
``__name__`` (a ``functools.partial``, a callable object) once raised ``AttributeError`` outside the
runner's ``try`` — the task died, polling went on, and no callback ever fired again."""
import asyncio
import functools
import unittest

from obcom.comunication.comunication_error import CommunicationTimeoutError
from obcom.comunication.cycle_query import ConditionalCycleQuery, _name_of
from test.comunication.test_cycle_query_delivery_race import ScriptedSolver, make_ok_response, make_request


class Recorder:
    """A callable object: no ``__name__``."""

    def __init__(self):
        self.values = []

    def __call__(self, responses):
        self.values.append(responses[0].value.v)


class TestCallbackNames(unittest.IsolatedAsyncioTestCase):

    async def deliver_to(self, *, sync=(), coroutines=()):
        crs = ScriptedSolver([[make_ok_response(v=1, ts=1.0)], [make_ok_response(v=2, ts=2.0)],
                              CommunicationTimeoutError(message='no more changes')])
        cq = ConditionalCycleQuery(crs=crs, list_request=[make_request()], delay=0.01, max_missed_msg=-1)
        for method in sync:
            cq.add_callback_method(method)
        for method in coroutines:
            cq.add_callback_async_method(method)
        cq.start()
        try:
            await asyncio.sleep(0.3)
        finally:
            cq.stop()
            await cq.stop_and_wait()

    async def test_partials_and_callable_objects_are_called(self):
        seen, recorder = [], Recorder()
        await self.deliver_to(sync=[functools.partial(lambda tag, r: seen.append((tag, r[0].value.v)), 'p'), recorder])
        self.assertEqual(seen, [('p', 1), ('p', 2)])
        self.assertEqual(recorder.values, [1, 2])

    async def test_an_async_partial_is_awaited(self):
        seen = []

        async def collect(tag, responses):
            seen.append((tag, responses[0].value.v))

        await self.deliver_to(coroutines=[functools.partial(collect, 'a')])
        self.assertEqual(seen, [('a', 1), ('a', 2)])

    def test_names(self):
        def deliver(r):
            pass
        self.assertTrue(_name_of(deliver).endswith('deliver'))
        self.assertIn('functools.partial', _name_of(functools.partial(deliver)))
        self.assertIn('Recorder', _name_of(Recorder()))


if __name__ == "__main__":
    unittest.main()
