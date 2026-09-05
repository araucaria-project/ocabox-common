import asyncio
import logging
import time
import warnings
from typing import Dict, List, Optional

from obcom.comunication.base_client_request_solver import BaseClientRequestSolver
from obcom.comunication.comunication_error import CommunicationRuntimeError, CommunicationTimeoutError
from obcom.comunication.error_policy import (
    Backoff,
    ErrorPolicy,
    SeverityAction,
    SeverityRule,
    ValuePolicy,
    _LogPolicyState,
)
from obcom.data_colection.response_error import ResponseError
from obcom.data_colection.value import Value
from obcom.data_colection.value_call import ValueRequest, ValueResponse
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__.rsplit('.')[-1])

# Delay used in the catch-all ``except Exception`` handler when the policy
# is SERVICE-style (i.e. NORMAL action = RETRY).  Exposed as a module
# constant so tests can patch it to 0 without touching asyncio.sleep.
_CATCH_ALL_RETRY_DELAY = 60.0
_ROUTER_TIMEOUT_REASON = 4002


class _SeverityRetryState:
    """Per-severity bookkeeping for one subscription.

    Tracks how many consecutive retries have happened for a given
    severity and when the streak started, so :class:`Budget` and
    :class:`LogPolicy` can do their work. Reset on every successful
    response — a subscription that recovers, then later hits the same
    severity again, gets the full first-N loud warnings rather than
    being stuck in throttled mode forever.
    """

    __slots__ = ('attempts', 'started_monotonic', 'log_state')

    def __init__(self, rule: SeverityRule) -> None:
        self.attempts: int = 0
        self.started_monotonic: float = time.monotonic()
        self.log_state: _LogPolicyState = rule.log.make_state()

    def reset(self, rule: SeverityRule) -> None:
        self.attempts = 0
        self.started_monotonic = time.monotonic()
        self.log_state = rule.log.make_state()


def _ignore_errors_to_policy(ignore_errors: bool) -> ErrorPolicy:
    """Translate the legacy ``ignore_errors`` flag into a policy.

    ``True`` was historically equivalent to "retry non-TEMPORARY errors
    forever, exactly like TEMPORARY"; ``False`` was the GUI-friendly
    default. Map onto the matching presets so existing callers see no
    behaviour change while we transition them to ``error_policy=``.
    """
    if ignore_errors:
        return ErrorPolicy.INTERACTIVE.with_overrides(
            normal=SeverityRule(action=SeverityAction.RETRY,
                                backoff=Backoff.immediate()),
            critical=SeverityRule(action=SeverityAction.RETRY,
                                  backoff=Backoff.immediate()),
        )
    return ErrorPolicy.INTERACTIVE


class BaseCycleQuery(ABC):
    """
    This is an abstract class that represents a circular query. It can only be created in a running asynchronous event
    loop or elsewhere when an asynchronous event loop is supplied in the argument. the class provides the methods
    'get_response', thanks to which you can wait for the next message for any number of tasks.

    After creating the circular query object, call the `start()` method to start communication with the server.
    To cancel the query, call the `stop()` method. The `stop()` method will be automatically called when the object
    is destroyed, but the event loop may still have tasks waiting to close.

    :param client: client object
    :param list_request: list of requests to cycle send
    :param delay: request delay - minimum interval between responses, default is getting from config file
    :param loop: async loop
    :param query_name: cycle query name used to distinguish queries in logs
    :param max_missed_msg: number of missed messages before stop cycle query. Default is give from config. It can be
        set from -1 to inf. If it is ste to -1 that mean isn't max missed messages and query will be renewing all time
    :param ignore_errors: flag to ignore errors. If the server returns an error other than temporary, the situation
        will be treated as a failed attempt and will be re-requested.
    :raise CommunicationRuntimeError: if not provide async loop and czn not get existing loop
    """

    DEFAULT_DELAY = 5
    DEFAULT_MAX_MISSED_MSG = 3
    DEFAULT_REQUEST_TIMEOUT = 30

    def __init__(self, crs: BaseClientRequestSolver, list_request: List[ValueRequest], delay: float or None = None,
                 loop=None, query_name: str = 'Default cycle query', max_missed_msg: int = None,
                 ignore_errors: bool = False, error_policy: Optional[ErrorPolicy] = None, **kwargs):
        self._query_name = query_name
        self._CRS: BaseClientRequestSolver = crs
        # Delivery sequence number: incremented exactly once per delivery
        # (``_notify_response``). Every consumer captures its own "seen"
        # anchor and compares it against this counter (see
        # ``_get_response_since``) instead of racing an ``asyncio.Event``'s
        # set/cleared edge — a consumer that is busy (running a slow
        # callback) or not yet parked when a delivery happens still
        # observes it the next time it checks. Because each consumer keeps
        # its own anchor, delivery is broadcast-safe: multiple concurrent
        # ``get_response()`` callers each see every delivery exactly once,
        # with no shared "claim" state for one waiter to steal from another.
        self._delivery_seq: int = 0
        self._last_response: List[ValueResponse] = []
        # Snapshot of ``_last_response`` taken exactly when the current
        # ``_delivery_seq`` was announced. ``_last_response`` itself keeps
        # getting overwritten by later, non-delivering loop iterations
        # (e.g. a timeout that resets it to ``[]`` without a fresh
        # delivery) while a busy consumer hasn't collected the previous
        # delivery yet — ``get_response`` must hand back what was actually
        # announced, not whatever ``_last_response`` happens to hold by
        # the time the busy consumer gets back to it.
        self._delivered_response: List[ValueResponse] = []
        # Resolved-and-replaced each delivery (see ``_notify_response``): a
        # future is broadcast-safe by construction (every waiter awaiting
        # it wakes on ``set_result``), unlike ``asyncio.Event.clear()``
        # which only the next waiter to check state effectively "consumes".
        self._delivery_fut: asyncio.Future or None = None
        # Guards against ``stop()`` calling ``_notify_response`` twice for
        # the same shutdown (e.g. once directly, again via
        # ``stop_and_wait``'s call to ``stop()`` while the cancelled task
        # has not finished unwinding yet) — a harmless double-set with the
        # old Event, but pointless double bookkeeping with the sequence.
        self._stop_notified: bool = False
        if delay is None or delay <= 0:
            delay = self.DEFAULT_DELAY
        self._delay: float = delay
        if max_missed_msg is None:
            max_missed_msg = self.DEFAULT_MAX_MISSED_MSG
        self._max_missed_msg: int = max_missed_msg  # can be number from -1 to inf
        self._task: asyncio.Task or None = None
        self._loop = loop
        self._set_loop()  # can raise CommunicationRuntimeError
        self._delivery_fut = self._loop.create_future()
        self._list_request: List[ValueRequest] = list_request
        self._additional_request_data = [{} for _ in range(
            len(self._list_request))]  # data to put to nex request in `request_data` dict
        self._errors: CommunicationRuntimeError or None = None
        self._stop_reason: Optional[ResponseError] = None
        self._callback_methods_a: list = []
        self._callback_methods: list = []
        self._callback_task: asyncio.Task or None = None
        # Resolve error policy. ``error_policy`` is the new public API;
        # ``ignore_errors`` is preserved for one release and translated
        # automatically when the new parameter is not set.
        if error_policy is not None and ignore_errors:
            warnings.warn(
                "Both 'error_policy' and 'ignore_errors' were set; 'ignore_errors' is ignored. "
                "'ignore_errors' is deprecated; use 'error_policy=ErrorPolicy.SERVICE' "
                "(or another preset) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        elif ignore_errors:
            warnings.warn(
                "'ignore_errors' is deprecated; use 'error_policy=ErrorPolicy.SERVICE' "
                "(or another preset) for daemons, or omit for the GUI-friendly default.",
                DeprecationWarning,
                stacklevel=2,
            )
        if error_policy is None:
            error_policy = _ignore_errors_to_policy(ignore_errors)
        self._error_policy: ErrorPolicy = error_policy
        self._severity_state: Dict[str, _SeverityRetryState] = {}
        # Per-subscription log-throttle state for the catch-all exception
        # handler, derived from the normal rule's LogPolicy so SERVICE
        # daemons get the same first_n=3/then_every_seconds=3600 behaviour
        # they already use for structured errors.
        self._catch_all_log_state: _LogPolicyState = error_policy.normal.log.make_state()

    def get_name(self):
        return self._query_name

    def __repr__(self):
        addresses = ' '.join(str(r.address).rsplit('.', maxsplit=1)[-1] for r in self._list_request)
        return f'{self._query_name} [{addresses}]'

    def _get_list_request_with_extinction(self) -> List[ValueRequest]:
        out = []
        for i, r in enumerate(self._list_request):
            new_r = r.copy()
            new_r.request_data.update(self._additional_request_data[i])
            out.append(new_r)
        return out

    async def get_response(self) -> List[ValueResponse]:
        """
        This method waits for the next response and returns it when it comes.

        On STOP (CRITICAL or spent retry budget), consumers receive one final
        delivery: the batch that caused the stop (`status=False`,
        `error=<ResponseError>`). After that `_errors` is set, `is_stopped()`
        becomes True, `stop_reason` is set, and no further deliveries follow.

        :raise CommunicationRuntimeError: when cycle request loop was stopped or message can't retrieve for other reason
        :return: new response as object ValueResponse
        """
        # Anchor "next" at the sequence value seen right now, at call time —
        # this call must wait for a delivery that happens AFTER it, matching
        # the documented "waits for the next response" contract. Multiple
        # concurrent callers each capture their own anchor here, so every
        # one of them independently observes the same delivery (broadcast),
        # instead of racing to consume a single shared "seen" counter.
        return await self._get_response_since(self._delivery_seq)

    async def _get_response_since(self, seen_seq: int) -> List[ValueResponse]:
        """Wait until a delivery has advanced past ``seen_seq``.

        Internal helper shared by the public ``get_response()`` (anchors at
        call time) and the callback runner (keeps its own long-lived anchor
        across iterations, so a delivery that happened while it was busy in
        a user callback is not lost — its anchor already lags behind
        ``_delivery_seq`` by the time it checks again).
        """
        if not self.is_stopped() and not self._task.done():
            while self._delivery_seq == seen_seq:
                # Shield: a bare Future cancellation propagates to every
                # waiter awaiting it (Task.cancel() cancels the future the
                # task is parked on). With one shared `_delivery_fut`, one
                # consumer's cancellation (a wait_for timeout, a closed
                # widget) would otherwise cancel every other consumer
                # parked on the same delivery. `shield()` over a bare
                # future creates no task — nothing executes and nothing
                # can be "abandoned" — it just gives each waiter its own
                # outer future to be cancelled through, while the shared
                # inner future stays alive for everyone else.
                await asyncio.shield(self._delivery_fut)
            if self._errors:
                raise self._errors
            return self._delivered_response
        raise CommunicationRuntimeError(message=f"{self}: Query was stopped. before waiting for a reply "
                                                f"you have to run them first")

    def _notify_response(self):
        """Advance the delivery sequence and wake every ``get_response`` waiter.

        Every place that used to call ``self._event.set()`` to announce a
        new (or final) response must call this instead. Resolves the
        current ``_delivery_fut`` and replaces it with a fresh one: a
        resolved future wakes every task awaiting it (broadcast-safe by
        construction), so no waiter can "steal" another waiter's wake-up
        the way one shared ``clear()`` could. Snapshots ``_last_response``
        at this exact moment into ``_delivered_response`` so a busy
        consumer that collects it later gets what was actually announced,
        immune to later non-delivering mutations of ``_last_response``.
        """
        self._delivery_seq += 1
        self._delivered_response = list(self._last_response)
        fut, self._delivery_fut = self._delivery_fut, self._loop.create_future()
        if not fut.done():
            fut.set_result(None)

    @abstractmethod
    async def _send_message(self):
        raise NotImplementedError

    def _change_time(self, time_now):
        for r in self._list_request:
            r.time_of_data = time_now

    def _run(self):
        self._task = self._loop.create_task(self._send_message())
        self._delivery_seq = 0
        self._delivered_response = []
        self._delivery_fut = self._loop.create_future()
        self._stop_notified = False

    def _set_loop(self):
        """

        :raise CommunicationRuntimeError:
        """
        if not self._loop:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.error(f"{self}: Can not get current async loop, something goes wrong")
                raise CommunicationRuntimeError(message='Can not get current async loop')

    def start(self):
        """
        Method starts cycle query if not started yet.
        """
        if self.is_stopped():
            self._stop_reason = None
            self._run()
            self._run_callbacks()
        else:
            logger.warning(f"{self}: This cycle query is already started")

    def stop(self):
        """Method stop cycle query."""
        if not self.is_stopped() and not self._task.done():
            self._task.cancel()
            if not self._stop_notified:
                self._stop_notified = True
                self._notify_response()
        if self._callback_task and self._callback_task in asyncio.all_tasks(
                self._loop) and not self._callback_task.done():
            self._callback_task.cancel()

    async def stop_and_wait(self):
        self.stop()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        try:
            await self._callback_task
        except asyncio.CancelledError:
            pass

    def is_stopped(self):
        """
        Method return false if cycle query is actually running.

        :return: False if running
        """
        if self._task and self._task in asyncio.all_tasks(self._loop):
            return False
        return True

    def __del__(self):
        self.stop()
        if not self.is_stopped():
            logger.warning(f"{self}: Object cycle query was destroyed before the main task exited. The "
                           f"main task has stopped but is still in the event loop. Before ending the program, you have "
                           f"to wait for it to be properly removed from the event loop.")

    def add_callback_async_method(self, method):
        """
        This method added a given method to list method with one run after cycle query retrieve a nev message
        On STOP (CRITICAL or spent retry budget), consumers receive one final
        callback with the stopping batch (`status=False`,
        `error=<ResponseError>`), then the query stops (`_errors` set,
        `is_stopped()` True, `stop_reason` set) and no further callbacks follow.
        :param method: asyncio method
        """
        self._callback_methods_a.append(method)

    @property
    def stop_reason(self) -> Optional[ResponseError]:
        """The server error that stopped this query (CRITICAL, or a retry budget spent on it).
        None while running, after a plain stop(), or when the stop came from the transport
        axis (missed messages, protocol error) — ``_errors`` carries those."""
        return self._stop_reason

    def add_callback_method(self, method):
        """
        This method added a given method to list method with one run after cycle query retrieve a nev message
        :param method: no async method
        """
        self._callback_methods.append(method)

    def _run_callbacks(self):
        self._callback_task = self._loop.create_task(self._execute_callbacks())

    async def _execute_callbacks(self):
        """Main loop for callback runner task"""
        run = True
        # Local anchor, initialized to 0 (not lazily captured on first use):
        # this must see a delivery that already happened before this task's
        # first turn (first-poll starvation) as well as one that happened
        # while this loop was busy awaiting a user callback below — in both
        # cases ``self._delivery_seq`` has already moved past this anchor by
        # the time we check, so ``_get_response_since`` returns immediately
        # instead of the delivery being lost.
        seen_seq = 0
        while run:
            # it is not necessary to check main task is still running because it is realized in 'get_response'
            await asyncio.sleep(0)
            try:
                result = await self._get_response_since(seen_seq)
                seen_seq = self._delivery_seq
            except CommunicationRuntimeError:
                run = False
                result = self._last_response
                # if exist last response then make callback last time but if not don't do it
                if not self._last_response:
                    return

            for a_method in self._callback_methods_a:
                if callable(a_method):
                    logger.debug(f"{self}: Execute callback {a_method.__name__}")
                    try:
                        await a_method(result)
                    except Exception as e:
                        # Catch broadly so a buggy callback cannot kill the
                        # callback runner task. If the exception escapes this
                        # try-except block, the task dies silently — the producer keeps
                        # polling (so is_stopped() stays False and no
                        # SUBSCRIPTION STOPPED log appears) but no further
                        # callbacks fire, leaving downstream consumers with
                        # stale data and no diagnostic signal.
                        #
                        # Do NOT widen to BaseException: CancelledError must
                        # propagate so task cancellation works correctly
                        # (CancelledError is BaseException since Python 3.8).
                        logger.exception(
                            f"{self}: async callback {a_method.__name__} raised "
                            f"unhandled {type(e).__name__}: {e}. Continuing — the "
                            f"subscription stays alive."
                        )

            for method in self._callback_methods:
                if callable(method):
                    logger.debug(f"{self}: Execute callback {method.__name__}")
                    try:
                        method(result)
                    except Exception as e:
                        logger.exception(
                            f"{self}: sync callback {method.__name__} raised "
                            f"unhandled {type(e).__name__}: {e}. Continuing."
                        )


class PeriodicCycleQuery(BaseCycleQuery):
    """
    This class represents a recurring query from the client side. Its purpose is to send the query to the server once
    in a certain period of time, no matter what the server returns. This class can only be created in a running async
    oop or elsewhere when an asynchronous event loop is specified in the argument. the class provides methods
    'get_response', thanks to which you can wait for the next message for any number of tasks.

    :param client: client object
    :param list_request: list of requests to cycle send
    :param delay: request delay - minimum interval between responses, default is getting from config file
    :param loop: async loop
    :param query_name: cycle query name used to distinguish queries in logs
    :param max_missed_msg: number of missed messages before stop cycle query. Default is give from config. It can be
        set from -1 to inf. If it is ste to -1 that mean isn't max missed messages and query will be renewing all time
    :param log_missed_msg: If is False missed messages will be skipped. Default False.
        Used only when is `only_new_data` set to False
    :raise CommunicationRuntimeError: if not provide async loop and czn not get existing loop
    """

    _DEFAULT_MIN_DELAY = 0.5

    def __init__(self, crs: BaseClientRequestSolver, list_request: List[ValueRequest], delay: float or None = None,
                 loop=None, log_missed_msg: bool = False, query_name: str = 'Default periodic query',
                 max_missed_msg: int = None, **kwargs):
        super().__init__(crs=crs, list_request=list_request, delay=delay, loop=loop, query_name=query_name,
                         max_missed_msg=max_missed_msg, **kwargs)
        self._log_missed_msg: bool = log_missed_msg
        self._min_delay = self._DEFAULT_MIN_DELAY
        if self._delay < self._min_delay:
            logger.warning(f"delay value is to low. Will by set to {self._min_delay}")

    async def _send_message(self):
        missed = 0
        start_time = time.time()
        self._errors = None
        while True:

            # wait before  send nex request
            wait_range = start_time + self._delay - time.time()
            if wait_range > 0:
                logger.debug(f"{self}: Wait {wait_range} before next request:{self}")
                await asyncio.sleep(wait_range)

            start_time = time.time()

            # move request time of data tolerance
            self._change_time(start_time)

            # make query
            try:
                requests = self._get_list_request_with_extinction()
                result = await self._CRS.send_request(requests=requests, timeout=start_time + self._delay,
                                                      no_wait=False)
                self._errors = None
                if result is None:
                    logger.error(f"{self}: Can not get response for giving request")
                    raise CommunicationRuntimeError(message="Can not get response for giving request, check "
                                                            "that the 'no_wait' flag is not set to true")

                self._last_response = result
                missed = 0
                self._notify_response()
            except CommunicationRuntimeError as e:
                self._errors = e
                self._last_response = []
                self._notify_response()
                break
            except CommunicationTimeoutError:
                missed += 1
                self._last_response = []
                logger.warning(f'{self}: The waiting time for the message: has expired. The router is not '
                               f'responding. Number of missing answers: {missed}')
                if self._log_missed_msg:
                    self._notify_response()

            except Exception as e:
                self._last_response = []
                msg = f'{self}: Unrecognized error in periodic cycle query: {type(e)}:{str(e)}'
                if self._catch_all_log_state.should_warn():
                    logger.error(msg, exc_info=True)
                else:
                    logger.debug(msg, exc_info=True)
                if self._error_policy.normal.action != SeverityAction.STOP:
                    await asyncio.sleep(_CATCH_ALL_RETRY_DELAY)
                    continue
                self._errors = CommunicationRuntimeError(message='Unrecognized error')
                self._notify_response()
                break

            if missed > self._max_missed_msg >= 0:
                logger.error(f"{self}: Too many missed messages at same time")
                self._errors = CommunicationRuntimeError(message='Too many missed messages at same time')
                self._notify_response()
                break
            await asyncio.sleep(0)


class ConditionalCycleQuery(BaseCycleQuery):
    """
    This is an advanced class of recursive query. It sends a query to the server that is intercepted by a special
    module that extends the cache memory capabilities of the server (the module is required to run this query).
    The response to the query is returned only when the value is changed and not more frequently than the given
    minimum time interval. This class can only be created in a running async loop or elsewhere when an asynchronous
    event loop is specified in the argument. the class provides 'get_response' methods, thanks to which you can wait
    for the next message for any number of tasks.

    An important aspect is the correct setting of the 'time_of_data tolerance' value, it indicates the minimum
    frequency with which the value should be refreshed on the server.

    :param client: client object
    :param list_request: list of requests to cycle send
    :param delay: request delay - minimum interval between responses, default is getting from config file
    :param loop: async loop
    :param query_name: cycle query name used to distinguish queries in logs
    :param max_missed_msg: number of missed messages before stop cycle query. Default is give from config. It can be
        set from -1 to inf. If it is ste to -1 that mean isn't max missed messages and query will be renewing all time
    :param request_timeout: The maximum waiting time for a response from the router, exceeding this time means that
        there are communication problems or the router is turned off. Recommended to leave the default value
    :param ignore_errors: flag to ignore errors. If the server returns an error other than temporary, the situation
        will be treated as a failed attempt and will be re-requested.
    :raise CommunicationRuntimeError: if not provide async loop and czn not get existing loop
    """

    def __init__(self, crs: BaseClientRequestSolver, list_request: List[ValueRequest], delay: float or None = None,
                 loop=None, query_name: str = 'Default conditional query', max_missed_msg: int = None,
                 request_timeout: float = None, ignore_errors: bool = False,
                 error_policy: Optional[ErrorPolicy] = None, **kwargs):
        super().__init__(crs=crs, list_request=list_request, delay=delay, loop=loop,
                         query_name=query_name, max_missed_msg=max_missed_msg,
                         ignore_errors=ignore_errors, error_policy=error_policy, **kwargs)
        if request_timeout is None:
            request_timeout = self.DEFAULT_REQUEST_TIMEOUT
        self._timeout: float = request_timeout
        for r in self._list_request:
            r.request_timeout = self._timeout
            r.cycle_query = True
        # ----- Staleness Contract (truth axis) -----
        # A declared value_policy is serialized into request_data so the
        # server (>= 2.5.2, which strips/consumes it) can mask errors on
        # the tolerance clock. Undeclared policy → nothing serialized,
        # nothing synthesized: pre-1.3.0 behaviour, safe with any server.
        if self._error_policy.value_policy is not None:
            for r in self._list_request:
                r.request_data['value_policy'] = self._error_policy.value_policy.value
                if r.time_of_data_max_age is None:
                    r.time_of_data_max_age = ValueRequest.default_max_age(r.time_of_data_tolerance)
        if self._stale_opt_in:
            # The long-poll window must not exceed the truth bound: while a
            # request is in flight the client is blind, so a dead router would
            # only be noticed at the request timeout. Capping the window at T2
            # bounds that detection latency; on a healthy connection the 4004
            # renewals then arrive within T2 and keep the contact clock alive
            # (no false staleness, old or new server). Floor of 1s so a very
            # tight explicit T2 does not turn into a renewal storm.
            window = max(1.0, self._tightest_t2())
            if window < self._timeout:
                self._timeout = window
                for r in self._list_request:
                    r.request_timeout = self._timeout
        # Last truthful value per request — feeds last_good/last_good_ts
        # tags of a synthesized stale-None.
        self._last_good: List[Optional[Value]] = [None] * len(self._list_request)
        # True after a stale-None was delivered; reset by the next real
        # value so each staleness episode wakes the client exactly once.
        self._stale_delivered: bool = False
        # The batch that opened the active staleness episode (synthesized
        # here or pushed by a >=2.6 server). While the episode lasts it IS
        # the delivered truth: raw error batches and empty timeout results
        # must not replace it in ``_last_response``.
        self._stale_view: Optional[list] = None
        # A 4004 renewal counts as healthy contact only when it arrived
        # after a real long-poll wait: an INSTANT 4004 (milliseconds instead
        # of ~the window) is the server refusing to do any work — e.g. its
        # reply margin exceeds the whole request window (obsrv#44) — and must
        # NOT feed the T2 clock, or a livelock of instant renewals starves
        # the consumer of both values and the honest stale-None (obcom#13).
        self._renewal_credible_after: float = 0.5 * self._timeout
        # The T2 clock runs from the last *healthy contact* — a batch of
        # real values or a 4004 long-poll renewal (server alive, value
        # unchanged-and-fresh). It deliberately does NOT run from the last
        # value *change*: a value stable for days, confirmed by renewals,
        # is perfectly truthful. Error batches and router silence do not
        # touch this clock. Monotonic: immune to NTP/manual clock jumps
        # (wall clock is used only for the emitted Value.ts). Exposed
        # publicly via ``last_healthy_contact_age``/``is_contact_fresh`` —
        # consumers building their own liveness watchdogs MUST use those
        # instead of timing deliveries.
        self._last_contact_ts: float = time.monotonic()
        # Router-silence warnings are throttled for opted-in subscriptions
        # (the tolerance clock, not the operator, owns the outage there).
        self._timeout_log_state: _LogPolicyState = self._error_policy.normal.log.make_state()
        self._starvation_episode_active: bool = False

    @property
    def last_healthy_contact_age(self) -> float:
        """Seconds (monotonic) since the last healthy contact with the source.

        Healthy contact = a delivered value batch or a CREDIBLE 4004 long-poll
        renewal (server alive, value unchanged-and-fresh). Errors, router
        silence and instant renewals do not count. This is the same clock the
        T2 staleness verdict runs on — consumers building their own liveness
        watchdogs MUST use this instead of timing deliveries (conditional
        deliveries are once-per-change: a stationary value is silent while
        perfectly healthy). Construction counts as the first contact, exactly
        as for the internal verdict: a source that never answers becomes
        stale one bound after start, not before (there is no value to
        misjudge until then).
        """
        return time.monotonic() - self._last_contact_ts

    def is_contact_fresh(self, max_age: Optional[float] = None) -> bool:
        """True when the source was known healthy within ``max_age`` seconds.

        ``max_age=None`` judges against :attr:`truth_bound` — the bound this
        subscription can actually vouch for. Pass an explicit value only when
        the consumer has a tighter (or looser) requirement of its own.
        """
        bound = self.truth_bound if max_age is None else max_age
        return self.last_healthy_contact_age <= bound

    @property
    def truth_bound(self) -> float:
        """Seconds of contact silence this subscription can still vouch for.

        ``max(long-poll window, declared T2)``. A healthy long poll is silent
        for up to one window (the server holds the request, refreshing at T1
        and confirming freshness with a 4004 renewal at the end), so contact
        older than the window is the first moment anything can be known about
        transport death — an undeclared subscription (window 30 s) honestly
        cannot vouch finer than that. A declared T2 already caps the window at
        T2 (floored at the 1 s transport resolution), so the bound is T2
        there. Consumers judging a subscription-fed value must use this bound
        (or ``is_contact_fresh()``), never the age of the last delivery or change.
        """
        declared = [r.time_of_data_max_age for r in self._list_request
                    if r.time_of_data_max_age is not None]
        return max(self._timeout, min(declared)) if declared else self._timeout

    def _detect_local_starvation(self, poll_started: Optional[float]):
        """Classify a timeout wake-up as local event-loop starvation.

        ``poll_started`` is monotonic, while the transport timeout sent to
        ``send_request`` is wall-clock; an NTP step can temporarily skew this
        comparison, which is acceptable for this local-health heuristic.
        """
        if poll_started is None:
            return False, 0.0, 0.0
        wake_lag = time.monotonic() - (poll_started + self._timeout)
        lag_threshold = max(0.5, 0.1 * self._timeout)
        return wake_lag > lag_threshold, wake_lag, lag_threshold

    async def _send_message(self):
        missed = 0
        not_clear_result = False
        self._errors = None
        poll_started = None
        while True:
            start_time = time.time()

            # update request data
            self._update_request_data()

            # make query
            try:
                not_clear_result = False
                requests = self._get_list_request_with_extinction()
                # one monotonic stamp serves both consumers: the renewal
                # credibility gate (obcom#13) and starvation detection (#11)
                poll_started = time.monotonic()
                result = await self._CRS.send_request(requests=requests, timeout=start_time + self._timeout,
                                                      no_wait=False)
                self._starvation_episode_active = False
                self._errors = None
                if result is None:
                    logger.error(f"{self}: Can not get response for giving request")
                    raise CommunicationRuntimeError(message="Can not get response for giving request, check "
                                                            "that the 'no_wait' flag is not set to true")
                self._last_response = result
                missed = 0
                not_clear_result = True  # if it gets some result return it for callback
                # Record truthful values; a fully-healthy batch ends the
                # current staleness episode (a later one may synthesize
                # again). A rich stale-None delivered by a >=2.6 server
                # (v=None + reason tag) is also healthy contact — and it
                # means the consumer is already informed, so client-side
                # synthesis must not duplicate it. Classification is
                # order-independent, and the batch-wide clock/flags move only
                # when the WHOLE batch is healthy (values, server-Nones, 4004
                # renewals): a mixed [healthy, error] stream must not mask
                # the erroring member forever — whole-batch semantics cut
                # both ways.
                saw_value = saw_server_none = False
                batch_healthy = all(
                    resp.status or (resp.error is not None and resp.error.code == 4004)
                    for resp in result)
                saw_credible_renewal = False
                for i, resp in enumerate(result[:len(self._last_good)]):
                    if resp.status and resp.value is not None:
                        if resp.value.v is not None:
                            self._last_good[i] = resp.value
                            saw_value = True
                        elif 'reason' in resp.value.tags:
                            saw_server_none = True
                        else:
                            # a genuine server-witnessed None value (no reason
                            # tag): unusual but legal — healthy contact ending
                            # any staleness episode; nothing to keep as
                            # last-good
                            saw_value = True
                if batch_healthy and (saw_value or saw_server_none):
                    self._last_contact_ts = time.monotonic()
                    if saw_value:
                        self._stale_delivered = False
                        self._stale_view = None
                        self._timeout_log_state.reset()
                    elif saw_server_none:
                        self._stale_delivered = True
                        self._stale_view = list(result)
                # ----- Error dispatch driven by self._error_policy -----
                # See ``error_policy.py`` for the action vocabulary
                # (RETRY / NOTIFY / STOP) and the per-severity rules. The
                # legacy ``ignore_errors`` flag is mapped onto a policy
                # at construction time, so this block only consults the
                # policy.
                continue_while = False           # outer-loop "go again, no callback fire"
                notify_then_continue = False     # outer-loop "fire callback first, then go again"
                retry_delay = 0.0                # backoff sleep before next attempt (RETRY/NOTIFY)
                successful_response = True       # reset per-severity state if no error
                last_error_code = None           # reason tag for a synthesized stale-None
                advanced_severities = set()      # charge each severity once per batch
                for r in self._last_response:
                    if r.status:
                        continue
                    successful_response = False
                    # 4004 (subscription expired) is a protocol heartbeat,
                    # not really an error — keep its dedicated silent retry.
                    if r.error and r.error.code == 4004:
                        logger.debug(f'{self}: address ({str(r.address)}) subscription expired - renewing')
                        # A renewal is a healthy heartbeat: the server is up
                        # and would have reported errors — the shown value is
                        # still truthful, so the T2 clock restarts. Gated on
                        # whole-batch health (a mixed [4004, error] batch must
                        # not postpone the erroring member's stale-None) AND
                        # on credibility: only a renewal that arrived after a
                        # real long-poll wait confirms anything; an instant
                        # 4004 is the server refusing work (see __init__).
                        if batch_healthy and (
                                time.monotonic() - poll_started >= self._renewal_credible_after):
                            self._last_contact_ts = time.monotonic()
                            saw_credible_renewal = True
                        if last_error_code is None:
                            # carrier for a stale-None synthesized during a
                            # renewal livelock; a real error found later in
                            # the scan overwrites it
                            last_error_code = 4004
                        # keep scanning: a later member may carry a real error
                        # that must set the action/reason (a CRITICAL after a
                        # 4004 must STOP, not be mistaken for a renewal)
                        continue_while = True
                        continue
                    if r.error is None:
                        # Response carried ``status=False`` without an
                        # error object — preserve the historical "stop"
                        # behaviour for that, since we have no severity
                        # to dispatch on.
                        raise CommunicationRuntimeError(
                            message=f"Client retrieve response without error object: {str(r)}")
                    severity = r.error.severity or ResponseError.SEVERITY_NORMAL
                    last_error_code = r.error.code
                    rule = self._error_policy.rule_for(severity)
                    state = self._severity_state.get(severity)
                    if state is None:
                        state = _SeverityRetryState(rule)
                        self._severity_state[severity] = state
                    if severity not in advanced_severities:
                        # attempts means "consecutive failing polls", so a
                        # batch with several same-severity members advances
                        # the counter (and its budget/backoff stage) once
                        advanced_severities.add(severity)
                        state.attempts += 1
                    action = rule.action
                    # Convert RETRY/NOTIFY → STOP if the budget is spent.
                    if (action != SeverityAction.STOP and rule.budget is not None
                            and rule.budget.is_exhausted(state.attempts, state.started_monotonic)):
                        logger.warning(
                            f'{self}: address ({str(r.address)}) retry budget exhausted '
                            f'(severity={severity}, attempts={state.attempts}); stopping subscription'
                        )
                        action = SeverityAction.STOP
                    if action == SeverityAction.STOP:
                        self._stop_reason = r.error
                        logger.error(
                            f'{self}: address ({str(r.address)}) stopped on severity={severity} '
                            f'code={r.error.code}: {r.error.message} — subscription stopped; '
                            f'fix the configuration and resubscribe')
                        raise CommunicationRuntimeError(
                            message=f"Client retrieve response with error: {str(r.error)}")
                    # RETRY or NOTIFY: log according to the rule's
                    # throttle (always emit DEBUG for forensics).
                    msg = (f'{self}: address ({str(r.address)}) error severity={severity} '
                           f'code={r.error.code}: {r.error.message} — retrying '
                           f'(attempt {state.attempts}, action={action.value})')
                    if state.log_state.should_warn():
                        logger.warning(msg)
                    else:
                        logger.debug(msg)
                    retry_delay = max(retry_delay, rule.backoff.delay(state.attempts))
                    if action == SeverityAction.NOTIFY:
                        notify_then_continue = True
                    else:
                        continue_while = True
                    # NO break: keep scanning — a later member may carry a
                    # more severe error (STOP raises above the moment it is
                    # seen; NOTIFY outranks RETRY at delivery time), and every
                    # erroring member is real evidence for its severity state.
                # Apply per-response value/protocol checks for the
                # status=True path (these mirror the historical code).
                if not continue_while and not notify_then_continue:
                    for r in self._last_response:
                        if not r.status:
                            continue
                        if r.value is not None and 'from_cf' not in r.value.tags:
                            logger.info(f'{self}: this address ({str(r.address)}) does not support cycle '
                                        f'conditional')
                            raise CommunicationRuntimeError(
                                message=f"this address ({str(r.address)}) does not support "
                                        f"recursive conditional queries")
                        if r.value is None:
                            logger.info(f'{self}: this address ({str(r.address)}) does not return any value')
                            raise CommunicationRuntimeError(
                                message=f"this address ({str(r.address)}) does not return any value")
                # All responses were successful → reset per-severity state
                # so the next failure starts the loud-warning streak fresh.
                if successful_response and self._severity_state:
                    self._severity_state.clear()
                if notify_then_continue:
                    # Past T2 the rich stale-None (which carries this error's
                    # code as `reason`) SUPERSEDES the raw error notify: one
                    # coherent delivery instead of two racing writes to
                    # _last_response within the same loop turn.
                    if self._maybe_synthesize_stale(reason=last_error_code):
                        await self._pulse_event()
                    elif self._hold_stale_view():
                        # Episode active: the stale-None already superseded
                        # this error, so raw notifies stay suppressed until a
                        # real value resets the episode — otherwise the
                        # promised Value(None) would be replaced by the raw
                        # error batch on the very next retry.
                        pass
                    else:
                        # Fire callback with the error response, then keep
                        # retrying. The delivery sequence (not a clear/set
                        # pulse) is what keeps this from re-delivering the
                        # same error in a tight loop across iterations.
                        self._notify_response()
                        await asyncio.sleep(0)
                    await self._backoff_sleep(retry_delay, last_error_code)
                    continue
                if continue_while:
                    # While the transport axis silently retries, the truth
                    # axis watches the T2 clock: masking an error is allowed
                    # only as long as the last value is still fresh enough
                    # for this client (Staleness Contract). The backoff wakes
                    # at the T2 deadline so the None stays punctual.
                    if self._maybe_synthesize_stale(reason=last_error_code):
                        await self._pulse_event()
                    else:
                        self._hold_stale_view()
                    await self._backoff_sleep(retry_delay, last_error_code)
                    await asyncio.sleep(0)
                    continue
                self._notify_response()
            except CommunicationRuntimeError as e:
                self._errors = e
                if not not_clear_result:
                    self._last_response = []
                self._notify_response()
                break
            except CommunicationTimeoutError:
                is_starvation, wake_lag, lag_threshold = self._detect_local_starvation(poll_started)
                if is_starvation:
                    # Our own loop was frozen: that says nothing about the
                    # source, so no verdict is made here — poll again and let
                    # the outcome decide (a value or credible renewal keeps
                    # the view truthful, a real silence is judged as such).
                    self._last_response = []
                    if not self._starvation_episode_active:
                        logger.warning(
                            f'{self}: event loop starved for {wake_lag:.3f}s '
                            f'(threshold {lag_threshold:.3f}s, {len(self._list_request)} subscriptions affected)')
                        self._starvation_episode_active = True
                    self._hold_stale_view()
                    await asyncio.sleep(0)
                    continue
                self._starvation_episode_active = False
                missed += 1
                self._last_response = []
                msg = (f'{self}: The waiting time for the message has expired. The router is not '
                       f'responding. Number of missing answers: {missed}')
                if not self._stale_opt_in:
                    logger.warning(msg)
                elif self._timeout_log_state.should_warn():
                    logger.warning(msg)
                else:
                    logger.debug(msg)
                # Router silence: the lowest layer that still has data is
                # this one, so stale-synthesis happens here (4002 =
                # "Application do not answer").
                if self._maybe_synthesize_stale(reason=_ROUTER_TIMEOUT_REASON):
                    await self._pulse_event()
                else:
                    self._hold_stale_view()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._last_response = []
                self._hold_stale_view()
                # Under a SERVICE-style policy (NORMAL action = RETRY), an
                # unexpected exception must not permanently kill the
                # subscription — a daemon is explicitly configured to
                # "retry forever", so breaking here defeats the whole
                # purpose.  Sleep a safe ceiling delay and keep the loop
                # alive so the subscription can recover once the underlying
                # condition clears.
                msg = f'{self}: Unrecognized error in conditional cycle query: {type(e)}:{str(e)}'
                if self._catch_all_log_state.should_warn():
                    logger.error(msg, exc_info=True)
                else:
                    logger.debug(msg, exc_info=True)
                if self._error_policy.normal.action != SeverityAction.STOP:
                    # The truth axis holds even for unrecognized failures: the
                    # deadline-aware sleep synthesizes the stale-None when T2
                    # expires mid-retry (a persistent internal error must not
                    # leave a DISPLAY consumer showing its old value forever).
                    await self._backoff_sleep(_CATCH_ALL_RETRY_DELAY, type(e).__name__)
                    continue
                self._errors = CommunicationRuntimeError(message='Unrecognized error')
                self._notify_response()
                break
            if (missed >= self._max_missed_msg >= 0
                    and self._error_policy.normal.action == SeverityAction.STOP):
                # The missed-message limit is a TRANSPORT-axis stop, so it
                # follows the transport identity (NORMAL action), not the
                # truth axis: FAIL_FAST/INTERACTIVE stop after the budget even
                # when value_policy delivered a None; retry-forever identities
                # (SERVICE/DISPLAY) nurse the connection indefinitely — the
                # truth axis meanwhile keeps the consumer honest via T2.
                logger.error(f"{self}: Too many missed messages at same time")
                self._errors = CommunicationRuntimeError(message='Too many missed messages at same time')
                self._notify_response()
                break
            await asyncio.sleep(0)

    def _tightest_t2(self) -> float:
        """Whole-batch truth bound: the tightest T2 in force across the batch."""
        return min(r.effective_max_age for r in self._list_request)

    @property
    def _stale_opt_in(self) -> bool:
        """True when this subscription declared ``ValuePolicy.NONE``.

        Only NONE turns on client-side stale synthesis: RAISE wants the
        error itself (severity actions), LAST_GOOD wants the aging value
        left in place — both are already what the historical loop does.
        """
        return self._error_policy.value_policy is ValuePolicy.NONE

    def _maybe_synthesize_stale(self, reason=None) -> bool:
        """Deliver a rich stale-None once the whole batch breaks tolerance.

        Whole-batch on purpose: everything handled here (router silence,
        server-side errors while retrying) affects the batch as one;
        per-address masking is the server freezer's job (Staleness
        Contract phase 2). Returns True when ``self._last_response`` now
        holds the synthesized batch and the caller should wake waiters.
        """
        if not self._stale_opt_in or self._stale_delivered:
            return False
        now = time.time()
        silence = time.monotonic() - self._last_contact_ts
        # Whole-batch synthesis triggers at the TIGHTEST bound: a late None
        # violates that member's truth bound, an early None for a looser
        # member is merely conservative (masking is permitted, not mandated).
        if silence <= self._tightest_t2():
            return False  # still truthful (within the tightest T2) — keep masking
        batch = []
        for i, r in enumerate(self._list_request):
            tags = {'reason': reason}
            last_good = self._last_good[i]
            # last_good tags only when a good value was ever seen: a startup
            # outage carries `reason` alone (the tags are documented-optional;
            # an explicit None would masquerade as a known last-good value).
            if last_good is not None:
                tags['last_good'] = last_good.v
                tags['last_good_ts'] = last_good.ts
            batch.append(ValueResponse(address=r.address, value=Value(v=None, ts=now, tags=tags),
                                       status=True, error=None))
        self._last_response = batch
        self._stale_delivered = True
        self._stale_view = batch
        # Drop the change bookkeeping: the server never witnessed this None,
        # so on the next successful contact it must redeliver the current
        # value unconditionally (it may be fresh even though we lost touch).
        for r in self._list_request:
            r.request_data.pop('time_of_known_change', None)
            r.request_data.pop('no_send_before', None)
        logger.info(f"{self}: tolerance exceeded — delivering stale-None (reason={reason})")
        return True

    async def _pulse_event(self):
        """Wake ``get_response`` waiters with the current response.

        Despite the name (kept for call-site continuity), this no longer
        pulses the event edge-triggered: it advances the delivery sequence
        (see ``_notify_response``) so a consumer that is busy or not yet
        parked in ``wait()`` still observes this delivery afterwards,
        instead of the batch being lost to a clear() that races it.
        """
        self._notify_response()
        await asyncio.sleep(0)

    def _hold_stale_view(self) -> bool:
        """Keep the active episode's None batch as the delivered truth.

        Returns True while a staleness episode is active (its rich None was
        already delivered); restores that batch into ``_last_response`` so
        neither a raw error batch nor an empty timeout result replaces the
        contract-compliant view. Errors stay suppressed until a real value
        resets the episode.
        """
        if not self._stale_delivered:
            return False
        if self._stale_view is not None:
            self._last_response = self._stale_view
        return True

    def _stale_deadline_remaining(self):
        """Seconds until the whole batch breaks T2, or None when synthesis
        is not applicable (undeclared/LAST_GOOD/already delivered)."""
        if not self._stale_opt_in or self._stale_delivered:
            return None
        silence = time.monotonic() - self._last_contact_ts
        return self._tightest_t2() - silence

    async def _backoff_sleep(self, retry_delay: float, reason) -> None:
        """Sleep the retry backoff, waking at the T2 deadline if it falls
        inside — the stale-None must be punctual even under a 10-60s staged
        backoff, not delayed until the next attempt."""
        if retry_delay <= 0:
            return
        remaining = self._stale_deadline_remaining()
        if remaining is not None and remaining < retry_delay:
            if remaining > 0:
                await asyncio.sleep(remaining)
            if self._maybe_synthesize_stale(reason=reason):
                await self._pulse_event()
            rest = retry_delay - max(remaining, 0)
            if rest > 0:
                await asyncio.sleep(rest)
        else:
            await asyncio.sleep(retry_delay)

    def _update_request_data(self):
        """
        This method repack last response data to nex request
        """
        if self._last_response:
            last_msg_vr = self._last_response
            for i, lm in enumerate(last_msg_vr):
                self._additional_request_data[i] = {}
                # A client-synthesized stale-None (v=None + reason tag,
                # no from_cf) is an answer, not an observed change — echoing
                # its ts as time_of_known_change would tell the server we
                # saw a change it never sent. A server-delivered stale-None
                # (from_cf) IS witnessed: echoing its ts is what stops the
                # server from re-delivering the same None every refresh.
                if lm.value is not None and (lm.value.v is not None
                                             or 'reason' not in lm.value.tags
                                             or 'from_cf' in lm.value.tags):
                    time_of_known_change = lm.value.ts
                    self._list_request[i].request_data['time_of_known_change'] = time_of_known_change
                    no_send_before = lm.value.ts + self._delay
                    self._list_request[i].request_data['no_send_before'] = no_send_before
                    self._list_request[i].cycle_query = True
                    self._change_time(no_send_before)
                # add subscription parameters returned by the server if error to resend
                if not lm.status and lm.error:
                    self._additional_request_data[i] = lm.error.kwargs
