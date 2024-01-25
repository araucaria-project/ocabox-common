import logging

from obcom.data_colection.coded_error import BaseCodedError

logger = logging.getLogger(__name__.rsplit('.')[-1])


class Address:

    def __init__(self, adr: str, **kwargs):
        if not isinstance(adr, str):
            raise AddressError(adr, code=1001, message='wrong type address.')
        self.adr = adr.split('.') if adr else []

    @classmethod
    def as_address(cls, a):
        """
        This method convert given value to Address object.

        :param a: value in anny type
        :return: Address
        """
        if isinstance(a, Address):
            return a
        if isinstance(a, str):
            return Address(a)
        raise ValueError(a)

    def __eq__(self, other):
        return self.adr == other.adr

    def __hash__(self):
        return hash(self.adr)

    def __str__(self):
        return '.'.join(self.adr)

    def __iter__(self):
        return iter(self.adr)

    def __getitem__(self, item):
        return self.adr[item]

    def __len__(self):
        return len(self.adr)

    def get_last_index(self):
        return len(self.adr) - 1

    def __copy__(self):
        return Address(self.__str__())

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if k == 'adr':
                v = '.'.join(self.adr)
            d[k] = v
        return d


class AddressError(BaseCodedError):
    """
    This is a base exception with code for Value errors.

    :ivar message: Error message
    :ivar code: Error code
    :ivar address: address
    :cvar CODE_GROUP: Number of error group, must be the number of whole thousands
    :cvar _KNOWN_LOCAL_CODES: List of known errors with local codes (codes without number of group can be number
        between 1 and 999)

    :param address: address
    :param code: Error code
    :param message: Error message
    """
    CODE_GROUP: int = 1000
    _KNOWN_LOCAL_CODES = [(1, 'Wrong address format'),
                          (2, 'Non-existent address'),
                          (3, 'Wrong parameters for address'),
                          (4, 'Access denied')]

    def __init__(self, address: Address or str = '', code: int = CODE_GROUP, message="", severity: str = None,
                 **kwargs):
        self.address = address
        if not message and address and (isinstance(address, Address) or isinstance(address, str)):
            message = f'The address {str(address)} is incorrect'
        super().__init__(message=message, code=code, severity=severity, **kwargs)
