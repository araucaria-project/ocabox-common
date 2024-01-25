import copy
import logging
from obcom.data_colection.coded_error import BaseCodedError

logger = logging.getLogger(__name__.rsplit('.')[-1])


class Value:

    def __init__(self, v, ts: float, value_type=None, tags: dict = None, **kwargs):
        self.v = v
        self.ts: float = ts
        self.type = value_type
        if tags is None:
            tags = {}
        self.tags: dict = tags

    def __eq__(self, other):
        return self.v == other.v

    def __gt__(self, other):
        return self.v > other.v

    def __lt__(self, other):
        return self.v < other.v

    # def __copy__(self):
    #     return Value(self.v, self.ts, self.type, self.tags)

    def is_expired(self, dt: float, tolerance: float = None):
        """
        This method check if date of this value is older than give date minus tolerance.

        :param dt: date what is needed
        :param tolerance: allowable time delay
        :return: true if date of this value is older what is needed.
        """
        if tolerance:
            dt = dt - tolerance
        return self.ts < dt  # id this date is older than date with is need then value is expired

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            d[k] = v
        return d

    def copy(self):
        return copy.deepcopy(self)


class TreeValueError(BaseCodedError):
    """
    This is a base exception with code for Value errors.

    :ivar message: Error message
    :ivar code: Error code
    :ivar value: value
    :cvar CODE_GROUP: Number of error group, must be the number of whole thousands
    :cvar _KNOWN_LOCAL_CODES: List of known errors with local codes (codes without number of group can be number
        between 1 and 999)

    :param value: value
    :param code: Error code
    :param message: Error message
    """
    CODE_GROUP: int = 2000
    _KNOWN_LOCAL_CODES = [(1, 'Default Value error'),
                          (2, 'Error creating value'),
                          (3, 'Too many retries to generate the value')]

    def __init__(self, value: Value or None = None, code: int = CODE_GROUP, message: str = "", severity: str = None,
                 **kwargs):
        self.value = value
        super().__init__(message=message, code=code, severity=severity, **kwargs)
