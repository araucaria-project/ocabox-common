import logging
from typing import List

import confuse
from abc import ABC
from obcom.comunication.comunication_error import CommunicationTimeoutError
from obcom.comunication.multipart_structure import MultipartStructure
from obcom.ob_config import SingletonConfig

logger = logging.getLogger(__name__.rsplit('.')[-1])


class BaseCommunicationObject(ABC):
    DEFAULT_NAME = 'BaseCommunicationObject'
    TYPE = 'default_communication_object'
    _SING_CONF = SingletonConfig

    def __init__(self, name: str = None, **kwargs):
        self.name = name if name else self.DEFAULT_NAME
        self.default_timeout = self.get_cfg('timeout')
        if self.default_timeout is None:
            logger.error(f"Can not find timeout value in settings")
            raise RuntimeError

    def get_cfg(self, name_cfg: str, default=None, use_default_settings=True):
        return self._get_cfg(name_cfg, default, use_default_settings)

    def get_cfg_deep(self, name_cfg: List[str], default=None, use_default_settings=True):
        return self._get_cfg_deep(name_cfg, default, use_default_settings)

    def _get_cfg_deep(self, name_cfg: List[str], default=None, use_default_settings=True):
        """
        The method looks deep for a value in the configuration file and returns it.

        :param name_cfg: list of names in dist config value
        :param default: default value if key not exist in config
        :param use_default_settings: use default settings if default value is None and can not get settings
        :return: config value or None if method can't find it
        """
        def build_request(name):
            c = self._SING_CONF.get_config()[self.TYPE][name]
            for n in name_cfg:
                c = c[n]
            return c
        try:
            value = build_request(self.name).get()
        except confuse.exceptions.NotFoundError:
            if default is None and use_default_settings:
                try:
                    value = build_request(self.DEFAULT_NAME).get()
                except confuse.exceptions.NotFoundError:
                    value = default
            else:
                value = default
        return value

    def _get_cfg(self, name_cfg: str, default=None, use_default_settings=True):
        """
        The method looks for a value in the configuration file and returns it.

        :param name_cfg: name of config value
        :param default: default value if key not exist in config
        :param use_default_settings: use default settings if default value is None and can not get settings
        :return: config value or None if method can't find it
        """
        return self._get_cfg_deep(name_cfg=[name_cfg], default=default, use_default_settings=use_default_settings)

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

