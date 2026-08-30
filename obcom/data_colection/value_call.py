import copy
import logging
import time
from dataclasses import dataclass, fields, field
from typing import ClassVar

from obcom.comunication.message_serializer import MessageSerializer
from obcom.data_colection.address import Address
from obcom.data_colection.response_error import ResponseError
from obcom.data_colection.tree_user import TreeUser, BaseTreeUser
from obcom.data_colection.value import Value

logger = logging.getLogger(__name__.rsplit('.')[-1])


@dataclass
class ValueExchange:
    """
    This is base class to representing communication inside tree
    """
    STANDARD_KEYS: ClassVar[list] = []  # master keys that store essential data
    _CONVERTING_TYPES: ClassVar[list] = [Address, Value, ResponseError, TreeUser]
    DEFAULT_REQUEST_TIMEOUT: ClassVar[float] = 30.0
    DEFAULT_TIME_OF_DATA_TOLERANCE: ClassVar[float] = 60.0

    @classmethod
    def from_dict(cls, dict_):
        """
        Create this class from given dict of fields. The dictionary must have all the necessary fields.
        Redundant fields are ignored.

        :param dict_: Dictionary witch class fields
        :raise TypeError: if the dictionary does not contain the required fields
        :raise AddressError: if the address is incorrect
        :raise ValueError: If the other value is invalid
        :return: instance of this class
        """
        class_fields = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dict_.items() if k in class_fields})  # why warning? everything is good

    @classmethod
    def from_byte(cls, bytes_: bytes):
        """
        Create this class from given bytes representing dict of fields. The dictionary must have all the necessary
        fields. Redundant fields are ignored.

        :param bytes_: Bytes representing dictionary witch class fields
        :raise TypeError: if the dictionary does not contain the required fields
        :raise AddressError: if the address is incorrect
        :raise ValueError: If the other value is invalid
        :return: instance of this class
        """
        dict_ = MessageSerializer.unpack_b(bytes_)
        return cls.from_dict(dict_)

    def to_dict(self) -> dict:
        """
        This method convert object to dictionary. If among the property of this class there are other classes that
        have to_dict() methods implanted, they will also be converted, creating a nested dictionary.

        :return: dictionary
        """
        d = {}
        for k, v in self.__dict__.items():
            if k in self.STANDARD_KEYS:
                if type(v) in self._CONVERTING_TYPES and hasattr(v, 'to_dict'):
                    d[k] = v.to_dict()
                else:
                    d[k] = v
        return d

    def to_byte(self) -> bytes:
        """
        This method convert object to bytes. If among the property of this class there are other classes that
        have to_dict() methods implanted, they will also be converted, creating a nested dictionary.

        :return: bytes
        """
        request_dict = self.to_dict()
        byt = MessageSerializer.pack_b(request_dict)
        return byt


@dataclass
class ValueRequest(ValueExchange):
    """
    This is a base object with representing request
    """
    # master keys that store essential data
    STANDARD_KEYS: ClassVar[list] = list(
        set(ValueExchange.STANDARD_KEYS + ['address', 'time_of_data', 'time_of_data_tolerance',
                                           'time_of_data_max_age', 'request_timeout',
                                           'request_type', 'request_data', 'user', 'cycle_query']))
    KNOWN_REQUEST_TYPES: ClassVar[list] = ['GET', 'PUT', 'EXECUTE']
    address: str or Address or dict
    time_of_data: float or None = None  # default - now
    time_of_data_tolerance: float or None = None  # default - get from config  :  moznaq zwrocic dane z zakresu (time_of_data - time_of_data_tolerance, now)
    # T2 of the temporal model (T0=delay, T1=time_of_data_tolerance, T2=this):
    # data younger than T1 is preferred, (T1, T2] is still an acceptable answer
    # while a refresh is being fought for, older than T2 must not be presented —
    # the Staleness Contract bound. None = undeclared (pre-contract behaviour;
    # cycle queries with a declared value_policy default it to 2*T1). A plain
    # dataclass field, NOT request_data: old servers drop it in from_dict, so
    # it can never leak to connectors (safe against the 2.3.12 bug class).
    time_of_data_max_age: float or None = None
    index: int = field(init=False, default=0)
    request_timeout: float or None = None  # absolute request timeout
    request_type: str = field(default='GET')
    request_data: dict or None = None
    user: BaseTreeUser = None
    cycle_query: bool = False

    def __post_init__(self):
        # check timeout
        if self.request_timeout is None:
            self.request_timeout = time.time() + ValueRequest.DEFAULT_REQUEST_TIMEOUT
        # check address
        if isinstance(self.address, dict):
            self.address = Address(**self.address)
        elif isinstance(self.address, Address):
            pass
        else:
            self.address = Address(self.address)  # I know, wrong typing but it must be so, raise AddressError here
        # check date
        if self.time_of_data is None:
            self.time_of_data = time.time()
        elif isinstance(self.time_of_data, float):
            pass
        else:
            raise ValueError
        # check time_of_data_tolerance
        if self.time_of_data_tolerance is None:
            self.time_of_data_tolerance = ValueRequest.DEFAULT_TIME_OF_DATA_TOLERANCE
        self.time_of_data_tolerance = float(self.time_of_data_tolerance)  # this raise ValueError if is wrong type
        # check time_of_data_max_age (T2): when declared it must not undercut T1
        if self.time_of_data_max_age is not None:
            self.time_of_data_max_age = float(self.time_of_data_max_age)
            if self.time_of_data_max_age < self.time_of_data_tolerance:
                logger.warning(f'time_of_data_max_age ({self.time_of_data_max_age}) < time_of_data_tolerance '
                               f'({self.time_of_data_tolerance}) for {self.address} — clamping to the tolerance')
                self.time_of_data_max_age = self.time_of_data_tolerance
        # check request type
        if self.request_type not in self.KNOWN_REQUEST_TYPES:
            raise ValueError
        # check request data
        if self.request_data is None:
            self.request_data = {}
        elif isinstance(self.request_data, dict):
            pass
        else:
            raise ValueError
        # check request user
        if self.user is None:
            self.user = TreeUser()
        elif isinstance(self.user, BaseTreeUser):
            pass
        elif isinstance(self.user, dict):
            self.user = TreeUser(**self.user)
        else:
            raise ValueError

    def copy(self):
        return copy.deepcopy(self)


@dataclass
class ValueResponse(ValueExchange):
    """
    This is a base object with representing response
    """
    # master keys that store essential data
    STANDARD_KEYS: ClassVar[list] = list(set(ValueExchange.STANDARD_KEYS + ['address', 'value', 'status', 'error']))
    address: str or Address
    value: Value or None = None
    status: bool = field(default=True)  # if false that mean response has some errors
    error: ResponseError or None = field(default=None)

    def __post_init__(self):
        if isinstance(self.address, dict):
            self.address = Address(**self.address)
        elif isinstance(self.address, Address):
            pass
        else:
            self.address = Address(self.address)  # I know, wrong typing but it must be so, raise AddressError here

        if isinstance(self.value, dict):
            self.value = Value(**self.value)
        elif self.value is None or isinstance(self.value, Value):
            pass
        else:
            raise ValueError

        if isinstance(self.error, dict):
            self.error = ResponseError(**self.error)
        elif self.error is None or isinstance(self.error, ResponseError):
            pass
        else:
            raise ValueError

    def __repr__(self):
        return f'{self.__class__.__name__}(adr={self.address}, val={self.value}, ' \
               f'status={self.status}, error={self.error})'
