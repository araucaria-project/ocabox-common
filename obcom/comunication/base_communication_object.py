import logging
from abc import ABC
from obcom.comunication.comunication_error import CommunicationTimeoutError
from obcom.comunication.multipart_structure import MultipartStructure

logger = logging.getLogger(__name__.rsplit('.')[-1])


class BaseCommunicationObject(ABC):
    DEFAULT_NAME = 'BaseCommunicationObject'
    TYPE = 'default_communication_object'

    def __init__(self, name: str = None, **kwargs):
        self._name = name if name else self.DEFAULT_NAME
        self.default_timeout = 30

    @property
    def name(self):
        return self._name

    def _get_time_to_expire(self, ms: MultipartStructure, use_default=False):
        """
        This method looks for the timeout value in multipart and returns it if it is not obsolete, otherwise it throws
        a CommunicationTimeoutError exception.

        :param ms: MultipartStructure object
        :param use_default: When set to true the default value will be taken if multipart does not contain a timeout
        value.
        :raise CommunicationTimeoutError: When the timeout value is obsolete
        :return: time to expire
        """
        try:
            time_to_expire = ms.time_to_expire()
            logger.debug(f"Time to expire from multipart: {time_to_expire}")
        except CommunicationTimeoutError:
            if not use_default:
                raise
            time_to_expire = self.default_timeout
            logger.debug(f"Default time to expire used: {time_to_expire}")

        if time_to_expire <= 0:
            raise CommunicationTimeoutError(
                message=f'The received message is already outdated. expiretime: {time_to_expire}')
        return time_to_expire

