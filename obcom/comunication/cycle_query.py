import asyncio
import logging
import time
from typing import List

from obcom.comunication.base_client_request_solver import BaseClientRequestSolver
from obcom.comunication.comunication_error import CommunicationRuntimeError, CommunicationTimeoutError
from obcom.data_colection.response_error import ResponseError
from obcom.data_colection.value_call import ValueRequest, ValueResponse
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__.rsplit('.')[-1])


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
    :raise CommunicationRuntimeError: if not provide async loop and czn not get existing loop
    """

    DEFAULT_DELAY = 5
    DEFAULT_MAX_MISSED_MSG = 3
    DEFAULT_REQUEST_TIMEOUT = 30

    def __init__(self, crs: BaseClientRequestSolver, list_request: List[ValueRequest], delay: float or None = None,
                 loop=None, query_name: str = 'Default cycle query', max_missed_msg: int = None, **kwargs):
        self._query_name = query_name
        self._CRS: BaseClientRequestSolver = crs
        # TODO sprawdziś czy nie ma problemów z synchronizacją przez Event
        self._event: asyncio.Event = asyncio.Event()
        self._last_response: List[ValueResponse] = []
        if delay is None or delay <= 0:
            delay = self.DEFAULT_DELAY
        self._delay: float = delay
        if max_missed_msg is None:
            max_missed_msg = self.DEFAULT_MAX_MISSED_MSG
        self._max_missed_msg: int = max_missed_msg  # can be number from -1 to inf
        self._task: asyncio.Task or None = None
        self._loop = loop
        self._set_loop()  # can raise CommunicationRuntimeError
        self._list_request: List[ValueRequest] = list_request
        self._additional_request_data = [{} for _ in range(len(self._list_request))]  # data to put to nex request in `request_data` dict
        self._errors: CommunicationRuntimeError or None = None
        self._callback_methods_a: list = []
        self._callback_methods: list = []
        self._callback_task: asyncio.Task or None = None

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

        :raise CommunicationRuntimeError: when cycle request loop was stopped or message can't retrieve for other reason
        :return: new response as object ValueResponse
        """
        if not self.is_stopped() and not self._task.done():
            await self._event.wait()
            await asyncio.sleep(0)  # let main task set event to false before return control to client's tasks
            if self._errors:
                raise self._errors
            return self._last_response
        raise CommunicationRuntimeError(message=f"{self}: Query was stopped. before waiting for a reply "
                                                f"you have to run them first")

    @abstractmethod
    async def _send_message(self):
        raise NotImplementedError

    def _change_time(self, time_now):
        for r in self._list_request:
            r.time_of_data = time_now

    def _run(self):
        self._task = self._loop.create_task(self._send_message())
        self._event.clear()

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
            self._run()
            self._run_callbacks()
        else:
            logger.warning(f"{self}: This cycle query is already started")

    def stop(self):
        """Method stop cycle query."""
        if not self.is_stopped() and not self._task.done():
            self._task.cancel()
            self._event.set()
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
        :param method: asyncio method
        """
        self._callback_methods_a.append(method)

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
        while run:
            # it is not necessary to check main task is still running because it is realized in 'get_response'
            await asyncio.sleep(0)
            try:
                result = await self.get_response()
            except CommunicationRuntimeError:
                run = False
                result = self._last_response
                # if exist last response then make callback last time but if not don't do it
                if not self._last_response:
                    return

            for a_method in self._callback_methods_a:
                if callable(a_method):
                    try:
                        logger.debug(f"{self}: Execute callback {a_method.__name__}")
                        await a_method(result)
                    except TypeError as e:
                        logger.error(f"Error in callback method {a_method.__name__}. Error: {e}")

            for method in self._callback_methods:
                if callable(method):
                    try:
                        logger.debug(f"{self}: Execute callback {method.__name__}")
                        method(result)
                    except TypeError as e:
                        logger.error(f"Error in callback method {method.__name__}. Error: {e}")


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
                self._event.set()
            except CommunicationRuntimeError as e:
                self._errors = e
                self._last_response = []
                self._event.set()
                break
            except CommunicationTimeoutError:
                missed += 1
                self._last_response = []
                logger.warning(f'{self}: The waiting time for the message: has expired. The router is not '
                               f'responding. Number of missing answers: {missed}')
                if self._log_missed_msg:
                    self._event.set()

            except Exception as e:
                self._last_response = []
                logger.error(f'{self}: Unrecognized error in cycle query: {str(e)}')
                self._errors = CommunicationRuntimeError(message=f'Unrecognized error')
                self._event.set()
                break

            if missed > self._max_missed_msg >= 0:
                logger.error(f"{self}: Too many missed messages at same time")
                self._errors = CommunicationRuntimeError(message='Too many missed messages at same time')
                self._event.set()
                break
            await asyncio.sleep(0)
            self._event.clear()


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
    :raise CommunicationRuntimeError: if not provide async loop and czn not get existing loop
    """

    def __init__(self, crs: BaseClientRequestSolver, list_request: List[ValueRequest], delay: float or None = None,
                 loop=None, query_name: str = 'Default conditional query', max_missed_msg: int = None,
                 request_timeout: float = None, **kwargs):
        super().__init__(crs=crs, list_request=list_request, delay=delay, loop=loop,
                         query_name=query_name, max_missed_msg=max_missed_msg, **kwargs)
        if request_timeout is None:
            request_timeout = self.DEFAULT_REQUEST_TIMEOUT
        self._timeout: float = request_timeout
        for r in self._list_request:
            r.request_timeout = self._timeout
            r.cycle_query = True

    async def _send_message(self):
        missed = 0
        not_clear_result = False
        self._errors = None
        while True:
            start_time = time.time()

            # update request data
            self._update_request_data()

            # make query
            try:
                not_clear_result = False
                requests = self._get_list_request_with_extinction()
                result = await self._CRS.send_request(requests=requests, timeout=start_time + self._timeout,
                                                      no_wait=False)
                self._errors = None
                if result is None:
                    logger.error(f"{self}: Can not get response for giving request")
                    raise CommunicationRuntimeError(message="Can not get response for giving request, check "
                                                            "that the 'no_wait' flag is not set to true")
                self._last_response = result
                missed = 0
                not_clear_result = True  # if it gets some result return it for callback
                # check errors
                continue_while = False  # this parameter is needed to continue the outer loop
                for r in self._last_response:
                    if not r.status and r.error and r.error.code == 4004:  # 4004 - timeout waiting for change value
                        # the value is not changed so client need refresh request and keep waiting
                        logger.debug(f'{self}: address ({str(r.address)}) subscription expired - renewing')
                        continue_while = True  # continue outer loop
                        break  # everything is ok, just router need confirmation that the client is alive
                    elif not r.status and r.error and r.error.severity == ResponseError.SEVERITY_TEMPORARY:
                        logger.warning(f'{self}: address ({str(r.address)}) subscription return error severity: '
                                       f'{ResponseError.SEVERITY_TEMPORARY}. Error message: {r.error.message}')
                        continue_while = True
                        break  # The server is returning a low priority error, need resubscribe
                    elif not r.status:  # One of the responses received has an error
                        raise CommunicationRuntimeError(message=f"Client retrieve response witch error: "
                                                                f"{str(r.error)}")

                    elif r.value is not None and 'from_cf' not in r.value.tags:
                        logger.info(f'{self}: this address ({str(r.address)}) does not support cycle '
                                    f'conditional')
                        raise CommunicationRuntimeError(message=f"this address ({str(r.address)}) does not support "
                                                                f"recursive conditional queries")
                    elif r.value is None:
                        logger.info(f'{self}: this address ({str(r.address)}) does not return any value')
                        raise CommunicationRuntimeError(message=f"this address ({str(r.address)}) does not return any "
                                                                f"value")
                if continue_while:
                    await asyncio.sleep(0)
                    continue
                self._event.set()
            except CommunicationRuntimeError as e:
                self._errors = e
                if not not_clear_result:
                    self._last_response = []
                self._event.set()
                break
            except CommunicationTimeoutError:
                missed += 1
                self._last_response = []
                logger.warning(f'{self}: The waiting time for the message has expired. The router is not '
                               f'responding. Number of missing answers: {missed}')
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._last_response = []
                logger.error(f'{self}: Unrecognized error in cycle query: {str(e)}')
                self._errors = CommunicationRuntimeError(message=f'Unrecognized error')
                self._event.set()
                break
            if missed >= self._max_missed_msg >= 0:
                logger.error(f"{self}: Too many missed messages at same time")
                self._errors = CommunicationRuntimeError(message='Too many missed messages at same time')
                self._event.set()
                break
            await asyncio.sleep(0)
            self._event.clear()

    def _update_request_data(self):
        """
        This method repack last response data to nex request
        """
        if self._last_response:
            last_msg_vr = self._last_response
            for i, lm in enumerate(last_msg_vr):
                self._additional_request_data[i] = {}
                if lm.value is not None:
                    time_of_known_change = lm.value.ts
                    self._list_request[i].request_data['time_of_known_change'] = time_of_known_change
                    no_send_before = lm.value.ts + self._delay
                    self._list_request[i].request_data['no_send_before'] = no_send_before
                    self._list_request[i].cycle_query = True
                    self._change_time(no_send_before)
                # add subscription parameters returned by the server if error to resend
                if not lm.status and lm.error:
                    self._additional_request_data[i] = lm.error.kwargs
