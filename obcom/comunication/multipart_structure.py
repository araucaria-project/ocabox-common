import time
from typing import List

from obcom.comunication.comunication_error import CommunicationTimeoutError

from obcom.comunication.message_serializer import MessageSerializer


class MultipartStructure:
    """
    This is a class that representing structure of messages sending between client and router
    """
    EMPTY_BYTE_1 = 0
    CREATE_TIME = 1
    ID_ = 2
    REQUEST_TIMEOUT = 3
    SERVICE_MSG = 4
    EMPTY_BYTE_2 = 5
    DATA = 6
    _MIN_SIZE = 7

    def __init__(self, multipart: List[bytes], prefix_size: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.multipart: List[bytes] = multipart
        self.prefix_size: int = prefix_size

    @property
    def prefix_data(self) -> List[bytes]:
        return self.get_prefix_data(self.multipart, self.prefix_size)

    @property
    def create_time(self):
        return self.get_create_time(self.multipart, self.prefix_size)

    @property
    def id_(self):
        return self.get_id(self.multipart, self.prefix_size)

    @property
    def request_timeout(self):
        return self.get_request_timeout(self.multipart, self.prefix_size)

    @property
    def request_timeout_float(self):
        return self.get_request_timeout_float(self.multipart, self.prefix_size)

    @property
    def service_msg(self):
        return self.get_service_msg(self.multipart, self.prefix_size)

    @property
    def service_msg_bool(self):
        return self.get_service_msg_bool(self.multipart, self.prefix_size)

    @property
    def data(self):
        return self.get_data(self.multipart, self.prefix_size)

    @staticmethod
    def get_prefix_data(multipart, prefix_size: int = 0) -> List[bytes]:
        return multipart[:prefix_size]

    @staticmethod
    def get_create_time(multipart, prefix_size: int = 0):
        return multipart[(MultipartStructure.CREATE_TIME + prefix_size)]

    @staticmethod
    def get_id(multipart, prefix_size: int = 0):
        return multipart[(MultipartStructure.ID_ + prefix_size)]

    @staticmethod
    def get_request_timeout(multipart, prefix_size: int = 0):
        return multipart[(MultipartStructure.REQUEST_TIMEOUT + prefix_size)]

    @staticmethod
    def get_request_timeout_float(multipart, prefix_size: int = 0):
        try:
            timeout = MessageSerializer.unpack_b(MultipartStructure.get_request_timeout(multipart=multipart,
                                                                                        prefix_size=prefix_size))
        except ValueError:
            timeout = None
        return timeout

    @staticmethod
    def get_service_msg(multipart, prefix_size: int = 0):
        return multipart[(MultipartStructure.SERVICE_MSG + prefix_size)]

    @staticmethod
    def get_service_msg_bool(multipart, prefix_size: int = 0) -> bool:
        try:
            msg_bool = MessageSerializer.unpack_b(MultipartStructure.get_service_msg(multipart=multipart,
                                                                                     prefix_size=prefix_size))
            msg_bool = bool(msg_bool)
        except ValueError:
            msg_bool = None
        return msg_bool

    @staticmethod
    def get_data(multipart, prefix_size: int = 0):
        return multipart[(MultipartStructure.DATA + prefix_size):]

    @staticmethod
    def create_multipart(create_time: bytes, id_: bytes, data: List[bytes], request_timeout: bytes = b'',
                         service_msg: bytes = b'\xc2', prefix_data: List[bytes] = None):
        prefix_data = [] if prefix_data is None else prefix_data
        return [*prefix_data, b'', create_time, id_, request_timeout, service_msg, b'', *data]

    @classmethod
    def from_parts(cls, create_time: bytes, id_: bytes, data: List[bytes], request_timeout: bytes = b'',
                   service_msg: bytes = b'\xc2', prefix_data: List[bytes] = None):
        return cls(cls.create_multipart(create_time=create_time, id_=id_, data=data, request_timeout=request_timeout,
                                        service_msg=service_msg, prefix_data=prefix_data), prefix_size=len(prefix_data))

    @staticmethod
    def validate_multipart(multipart: List[bytes], ps: int = 0):
        """
        This method validate multipart.

        :param multipart: multipart
        :param ps: prefix size
        :raise ValueError: when is something wrong witch multipart
        :return: True if ok
        """
        MS = MultipartStructure
        if len(multipart) < MultipartStructure._MIN_SIZE + ps:
            raise ValueError(f'Wrong multipart because is shorter than {MS._MIN_SIZE + ps}')
        if multipart[MS.EMPTY_BYTE_1 + ps] or multipart[MS.EMPTY_BYTE_2 + ps]:
            raise ValueError(f'Empty byte in multipart is not empty')
        if not multipart[MS.CREATE_TIME + ps]:
            raise ValueError(f'Request has not message create date')
        if not multipart[MS.ID_ + ps]:
            raise ValueError(f'Request has not message id')
        if not multipart[MS.REQUEST_TIMEOUT + ps]:
            raise ValueError(f'Request has not message timeout')
        if not multipart[MS.SERVICE_MSG + ps]:
            raise ValueError(f'Request has not service msg')
        if not multipart[MS.DATA + ps]:
            raise ValueError(f'No data in multipart')
        return True

    def validate(self):
        """
        This method validate multipart storage in class.

        :raise ValueError: when is something wrong witch multipart
        :return: True if ok
        """
        self.validate_multipart(self.multipart, self.prefix_size)

    def is_expire(self, present_time: float = None):
        return self.is_expire_multipart(multipart=self.multipart, present_time=present_time,
                                        prefix_size=self.prefix_size)

    @staticmethod
    def is_expire_multipart(multipart, present_time: float = None, prefix_size: int = 0):
        return MultipartStructure.get_time_to_expire(multipart=multipart, present_time=present_time,
                                                     prefix_size=prefix_size) <= 0

    def time_to_expire(self, present_time: float = None):
        return self.get_time_to_expire(self.multipart, present_time=present_time, prefix_size=self.prefix_size)

    @staticmethod
    def get_time_to_expire(multipart, present_time: float = None, prefix_size: int = 0):
        if not present_time:
            present_time = time.time()
        try:
            timeout = MessageSerializer.unpack_b(
                MultipartStructure.get_request_timeout(multipart=multipart, prefix_size=prefix_size))
        except ValueError:
            raise CommunicationTimeoutError(message='The received message have not timeout value.')
        time_to_expire = timeout - present_time
        if time_to_expire > 0:
            return time_to_expire
        return 0
