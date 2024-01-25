import logging
from abc import ABC
from typing import List, Tuple

logger = logging.getLogger(__name__.rsplit('.')[-1])


class ErrorSeverity(ABC):
    """
    It's a class that stores error severities.

    :cvar SEVERITY_LEVELS: severity levels
    :cvar SEVERITY_NORMAL: severity normal
    :cvar SEVERITY_CRITICAL: severity critical
    :cvar SEVERITY_TEMPORARY: severity temporary
    """
    SEVERITY_NORMAL: str = 'NORMAL'
    SEVERITY_CRITICAL: str = 'CRITICAL'
    SEVERITY_TEMPORARY: str = 'TEMPORARY'
    SEVERITY_LEVELS: list = [SEVERITY_NORMAL, SEVERITY_CRITICAL, SEVERITY_TEMPORARY]
    SEVERITY_HIERARCHY: list = [SEVERITY_TEMPORARY, SEVERITY_NORMAL, SEVERITY_CRITICAL]

    @classmethod
    def compare_severity(cls, severity_code1: str, severity_code2: str) -> bool:
        """
        Method compare two severity levels and return true if first is more important than second

        :param severity_code1: first severity level
        :param severity_code2: second severity level

        :raise ValueError: if given severity_level is not known

        :return: true if first is greater
        """
        try:
            index1 = cls.SEVERITY_HIERARCHY.index(severity_code1)
            index2 = cls.SEVERITY_HIERARCHY.index(severity_code2)
        except ValueError:
            raise
        return index1 > index2


class BaseCodedError(Exception, ErrorSeverity, ABC):
    """
    This is a base exception with code.

    :ivar message: Error message
    :ivar code: Error code
    :cvar severity: severity level
    :cvar kwargs: some additional data
    :cvar CODE_GROUP: Number of error group, must be the number of whole thousands
    :cvar _KNOWN_LOCAL_CODES: List of known errors with local codes (codes without number of group can be number
        between 1 and 999)
    :cvar SEVERITY_LEVELS: severity levels
    :cvar SEVERITY_NORMAL: severity normal
    :cvar SEVERITY_CRITICAL: severity critical
    :cvar SEVERITY_TEMPORARY: severity temporary

    :param code: Error code
    :param message: Error message
    :param severity: severity level
    """
    CODE_GROUP: int = 0
    # This is list of pair (code, description) code can be number between 1 and 999
    _KNOWN_LOCAL_CODES: List[Tuple[int, str]] = []
    ERROR_CODE: List[Tuple[int, str]] = [(c + CODE_GROUP, d) for c, d in _KNOWN_LOCAL_CODES]

    def __init__(self, code: int = CODE_GROUP, message: str = "", severity: str = None, **kwargs):
        if severity is None:
            severity = self.SEVERITY_NORMAL
        if message:
            message = message
        elif code != self.CODE_GROUP:
            message = f'({str(code)}) {self.get_code_description(code)}'
        else:
            message = "Default error."
        self.message = message
        self.code = code
        self.severity: str = severity
        self.kwargs = kwargs
        super().__init__(self.message)

    @classmethod
    def get_global_error_codes(cls):
        return [(c + cls.CODE_GROUP, d) for c, d in cls._KNOWN_LOCAL_CODES]

    @classmethod
    def is_code_known(cls, code: int) -> bool:
        """
        Method returns true if given code is known
        :param code: code to check
        :return: true if code is known and false if not
        """
        for c, m in cls.get_global_error_codes():
            if code == c:
                return True
        return False

    @classmethod
    def get_code_description(cls, code) -> str:
        """
        Method returns description for given code if code is known or empty string if not.
        :param code: code
        :return: description for given code or empty string
        """
        for c, m in cls.get_global_error_codes():
            if code == c:
                return m
        return ''


class TreeStructureError(BaseCodedError):
    """
    This is a base exception with code for project structure errors.

    :ivar message: Error message
    :ivar code: Error code
    :cvar CODE_GROUP: Number of error group, must be the number of whole thousands
    :cvar _KNOWN_LOCAL_CODES: List of known errors with local codes (codes without number of group can be number
        between 1 and 999)

    :param code: Error code
    :param message: Error message
    """
    CODE_GROUP: int = 3000
    _KNOWN_LOCAL_CODES = [(1, 'Wrong tree architecture, unexpected end of the branch, no next component'),
                          (2, 'Component has not implemented method or method work incorrect')]

    def __init__(self, code: int = CODE_GROUP, message="", severity: str = None, **kwargs):
        super().__init__(message=message, code=code, **kwargs)


class TreeOtherError(BaseCodedError):
    CODE_GROUP = 4000
    _KNOWN_LOCAL_CODES = [(1, 'Wrong request'),
                          (2, 'Application do not answer'),
                          (3, 'This request cannot be subscribed. The cache does not store the value for this request'),
                          (4, 'The time to generate the value has been exceeded'),
                          (5, 'The module could not connect to the external service.'),
                          (6, 'Incorrectly calculated request timeout'),
                          (7, 'Wrong argument'),
                          ]

    def __init__(self, code: int = CODE_GROUP, message="", severity: str = None, **kwargs):
        super().__init__(message=message, code=code, severity=severity, **kwargs)
