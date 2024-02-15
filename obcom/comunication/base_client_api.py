import logging
import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional

from obcom.comunication.base_client_request_solver import BaseClientRequestSolver
from obcom.comunication.comunication_error import CommunicationTimeoutError, CommunicationRuntimeError
from obcom.comunication.cycle_query import ConditionalCycleQuery, BaseCycleQuery, PeriodicCycleQuery
from obcom.data_colection.address import Address
from obcom.data_colection.tree_user import BaseTreeUser
from obcom.data_colection.value_call import ValueRequest, ValueResponse

logger = logging.getLogger(__name__.rsplit('.')[-1])


class BaseClientAPI(ABC):

    @property
    @abstractmethod
    def user(self) -> BaseTreeUser:
        raise NotImplementedError

    @property
    @abstractmethod
    def _CRS(self) -> BaseClientRequestSolver:
        raise NotImplementedError

    async def get_async(self, address, time_of_data: float or None = None,
                        time_of_data_tolerance: float or None = None,
                        request_timeout: float or None = None,
                        parameters_dict: dict = None) -> ValueResponse or None:
        if parameters_dict is None:
            parameters_dict = {}
        request = ValueRequest(address=address,
                               time_of_data=time_of_data,
                               time_of_data_tolerance=time_of_data_tolerance,
                               request_timeout=request_timeout,
                               request_data=parameters_dict,
                               user=self.user)
        return await self.send_single(request)

    async def put_async(self, address, time_of_data: float or None = None,
                        time_of_data_tolerance: float or None = None,
                        request_timeout: float or None = None,
                        parameters_dict: dict = None, no_wait=True) -> ValueResponse or None:
        if parameters_dict is None:
            parameters_dict = {}
        request = ValueRequest(address=address,
                               time_of_data=time_of_data,
                               time_of_data_tolerance=time_of_data_tolerance,
                               request_timeout=request_timeout,
                               request_type='PUT',
                               request_data=parameters_dict,
                               user=self.user)
        return await self.send_single(request, no_wait=no_wait)

    async def send_single(self, request: ValueRequest, no_wait: bool = False) -> ValueResponse or None:
        vr = await self.send_multi([request], no_wait=no_wait)
        if not no_wait:
            return vr[0]
        return None

    async def send_multi(self, requests: List[ValueRequest], no_wait: bool = False) -> Optional[List[ValueResponse]]:
        """

        :param no_wait: If 'true' than request will be sent and client will not wait for response
        :param requests: request
        :raise CommunicationRuntimeError:
        :raise CommunicationTimeoutError:
        :return:
        """
        shortest_timeout = None
        for r in requests:
            if r.request_timeout and (shortest_timeout is None or r.request_timeout < shortest_timeout):
                shortest_timeout = r.request_timeout
            if not r.user:
                r.user = self.user
        if shortest_timeout is None:
            logger.error(f"Unable to get timeout value from request. Request is uncompleted.")
        try:
            resp = await self._CRS.send_request(requests=requests, timeout=shortest_timeout, no_wait=no_wait)
        except CommunicationRuntimeError:
            raise
        except CommunicationTimeoutError:
            raise
        return resp

    async def subscribe(self, address: str or Address, time_of_data_tolerance: float or None = None,
                        delay: float or None = None, parameters_dict: dict = None,
                        name: str = 'Default_subscription', max_missed_msg: int = None) -> BaseCycleQuery:
        """
        This method creates a cycle query that only returns new values. The `ConditionalCycleQuery` object is
        created and returned.

        :param address: address
        :param time_of_data_tolerance: how old data can be returned. This time should be greater than that specified
            in config file for `TreeConditionalFreezer` object
        :param delay: request delay, minimum interval between responses
        :param parameters_dict: dict of parameters to send witch request GET/PUT/etc...
        :param name: name of the subscription
        :param max_missed_msg: more information in :class:`.cycle_query.ConditionalCycleQuery`
        :return: object `ConditionalCycleQuery`
        """
        if time_of_data_tolerance is None and delay:
            time_of_data_tolerance = delay
        if parameters_dict is None:
            parameters_dict = {}
        request = ValueRequest(address=address,
                               time_of_data_tolerance=time_of_data_tolerance,
                               request_data=parameters_dict,
                               user=self.user)
        CQ_API = ConditionalCycleQuery(crs=self._CRS, list_request=[request], delay=delay,
                                       max_missed_msg=max_missed_msg, name=name)
        return CQ_API

    async def subscribe_with_callback(self, address: str or Address, time_of_data_tolerance: float or None = None,
                                      delay: float or None = None, parameters_dict: dict = None,
                                      name: str = 'Default_subscription', max_missed_msg: int = None,
                                      callback_method=None, async_callback_method=None) -> BaseCycleQuery:
        """
        This method creates a cycle query that only returns new values. The `ConditionalCycleQuery` object is
        created, started and returned.

        :param address: address
        :param time_of_data_tolerance: how old data can be returned. This time should be greater than that specified
            in config file for `TreeConditionalFreezer` object
        :param delay: request delay, minimum interval between responses
        :param parameters_dict: dict of parameters to send witch request GET/PUT/etc...
        :param name: name of the subscription
        :param max_missed_msg: more information in :class:`.cycle_query.ConditionalCycleQuery`
        :param callback_method: method with one will be run after CycleQuery retrieve message from server
        :param async_callback_method: async method with one will be run after CycleQuery retrieve message from server
        :return:
        """
        cq = await self.subscribe(address=address, time_of_data_tolerance=time_of_data_tolerance, delay=delay,
                                  parameters_dict=parameters_dict, name=name, max_missed_msg=max_missed_msg)
        if callback_method is not None:
            cq.add_callback_method(callback_method)
        if async_callback_method is not None:
            cq.add_callback_async_method(async_callback_method)
        cq.start()
        return cq

    async def run_subscription_callbacks(self, address: str or Address, time_of_data_tolerance: float or None = None,
                                         delay: float or None = None, parameters_dict: dict = None,
                                         name: str = 'Default_subscription', max_missed_msg: int = None,
                                         callback_method=None, async_callback_method=None):
        cq = await self.subscribe_with_callback(address=address, time_of_data_tolerance=time_of_data_tolerance,
                                                delay=delay,
                                                parameters_dict=parameters_dict, name=name,
                                                max_missed_msg=max_missed_msg,
                                                callback_method=callback_method,
                                                async_callback_method=async_callback_method)
        try:
            while True:
                await asyncio.sleep(0)
                await cq.get_response()
                # todo w przyszłości pomyśleć nad przeniesieniem nieskończonej pętli do CycleQuery i zastąpieniem jej
                #  przez Condition które jest uwalniane po zamknięciu CycleQuery
        except CommunicationRuntimeError as e:
            logger.error(f"updater named {name} receive CommunicationRuntimeError: {e}")
        finally:
            await cq.stop_and_wait()  # it is recommended to use this method instead of the usual Stop() because it
            # waits for the query to finish and the stop method only puts it in a closing state which can take some
            # time and must be terminated before the end of the program

    async def send_cycle_multipart(self, address: str or Address, time_of_data_tolerance: float or None = None,
                                   delay: float or None = None, parameters_dict: dict = None,
                                   name: str = 'Default_cycle_request', max_missed_msg: int = None,
                                   log_missed_msg: bool = False) -> BaseCycleQuery:
        """
        This method creates a cycle query that returns a value once per specified interval of time (`delay`).

        :param address: address
        :param time_of_data_tolerance: how old data can be returned.
        :param delay: request delay
        :param log_missed_msg: If is False missed messages will be skipped. Default False
        :param parameters_dict: dict of parameters to send witch request GET/PUT/etc...
        :param name: name of the subscription
        :param max_missed_msg: more information in :class:`.cycle_query.ConditionalCycleQuery`
        :return: object PeriodicCycleQuery
        """
        if not time_of_data_tolerance and delay:
            time_of_data_tolerance = delay
        if parameters_dict is None:
            parameters_dict = {}
        request = ValueRequest(address=address,
                               time_of_data_tolerance=time_of_data_tolerance,
                               request_data=parameters_dict,
                               user=self.user)
        CQ_API = PeriodicCycleQuery(crs=self._CRS, list_request=[request], delay=delay,
                                    max_missed_msg=max_missed_msg, name=name, log_missed_msg=log_missed_msg)
        return CQ_API

    @abstractmethod
    async def server_is_alive(self, request_timeout: float = None):
        raise NotImplementedError

    @abstractmethod
    async def server_reload_nats_config(self, request_timeout: float = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_cfg(self, name_cfg: str, default=None, use_default_settings=True):
        raise NotImplementedError

    @abstractmethod
    def get_cfg_deep(self, name_cfg: List[str], default=None, use_default_settings=True):
        raise NotImplementedError
