import logging
from abc import ABC, abstractmethod
from typing import List

from obcom.data_colection.value_call import ValueRequest, ValueResponse

logger = logging.getLogger(__name__.rsplit('.')[-1])


class BaseClientRequestSolver(ABC):

    @abstractmethod
    async def send_request(self, requests: List[ValueRequest], timeout: float = None,
                           no_wait: bool = False) -> List[ValueResponse]:
        raise NotImplementedError
