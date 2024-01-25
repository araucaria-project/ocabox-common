import logging

from obcom.data_colection.address import AddressError
from obcom.data_colection.coded_error import TreeStructureError, TreeOtherError, BaseCodedError, ErrorSeverity
from obcom.data_colection.value import TreeValueError

logger = logging.getLogger(__name__.rsplit('.')[-1])


class ResponseError(ErrorSeverity):
    SUPPORTED_ERRORS = [TreeValueError, TreeStructureError, AddressError, TreeOtherError]

    def __init__(self, code: int, message: str, component_name: str, severity: str = None, **kwargs):
        if severity is None:
            severity = self.SEVERITY_NORMAL
        self.code: int = code
        self.message: str = message
        self.component_name: str = component_name
        self.severity: str = severity
        self.kwargs = kwargs

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if k == 'kwargs':
                d = dict(d, **v)
            else:
                d[k] = v
        return d

    def __copy__(self):
        return ResponseError(self.code, self.message, self.component_name, self.severity, **self.kwargs)

    def __str__(self):
        return f'ResponseError ({self.severity}) code: {str(self.code)} - ' \
               f'{self.message if self.message else self.get_code_description(self.code)}'

    @classmethod
    def is_code_known(cls, code: int):
        for e in cls.SUPPORTED_ERRORS:
            if e.is_code_known(code):
                return True
        return False

    @classmethod
    def get_code_description(cls, code):
        for e in cls.SUPPORTED_ERRORS:
            desc = e.get_code_description(code)
            if desc:
                return desc
        return ''

    @classmethod
    def get_code_error(cls, code):
        for e in cls.SUPPORTED_ERRORS:
            if e.is_code_known(code):
                return e
        return None

    @classmethod
    def from_coded_error(cls, component_name: str, err: BaseCodedError):
        """
        Method create ResponseError from BaseCodedError.

        :param component_name: the name of the reporting component
        :param err: instance of BaseCodedError

        :return: instance of ResponseError
        """
        new_ResErr = ResponseError(code=err.code, message=err.message, component_name=component_name,
                                   severity=err.severity, **err.kwargs)
        return new_ResErr
