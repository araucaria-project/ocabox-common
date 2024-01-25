import logging
from abc import ABC
from typing import List
from zmq.asyncio import Context
from obcom.comunication.base_communication_object import BaseCommunicationObject
from obcom.comunication.multipart_structure import MultipartStructure

logger = logging.getLogger(__name__.rsplit('.')[-1])


class BaseZmqCommunicationObject(BaseCommunicationObject, ABC):
    DEFAULT_NAME = 'BaseCommunicationZmqObject'
    TYPE = 'default_communication_zmq_object'

    def __init__(self, name: str = None, port: int = None, **kwargs):
        super().__init__(name=name, **kwargs)

        self._port = port if port else self._get_cfg('port')
        if not self._port or not isinstance(self._port, int):
            logger.error(f"Can not get correct port ({self._port}) for {self.TYPE}")
            raise RuntimeError
        # OMQ
        self.context = Context()
        self._front_socket = None

    @staticmethod
    def _open_envelope(multipart: List[bytes]) -> MultipartStructure:
        raise NotImplementedError

    @staticmethod
    def _pack_to_envelope(target: List[bytes], create_time: bytes, msg_id: bytes, request_timeout: bytes,
                          service_msg: bytes, data_b: list) -> MultipartStructure:
        """

        :param target: address of clients should be one address or many witch empty byte separate.
        :param create_time: request create time
        :param msg_id: request id
        :param data_b: list sub-requests
        :return: multipart
        """
        raise NotImplementedError

    def __del__(self):
        self.context.destroy()  # without this zmq not close socket when BaseCommunicationObject is destroyed
