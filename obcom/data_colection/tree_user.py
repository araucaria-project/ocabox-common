import time
import uuid


class BaseTreeUser:
    def __init__(self, name: str = '', **kwargs):
        self._user_id: bytes = b''
        self.login_date: float = time.time()
        self.name: str = name

    @property
    def id_(self) -> bytes:
        return self._user_id

    def __eq__(self, other):
        return self.id_ == other.id_

    def __str__(self):
        return self.name

    def __bytes__(self):
        return self.id_

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if k == '_user_id':
                continue
            if k == 'login_date':
                continue
            d[k] = v
        return d

    def set_id(self, id_: bytes):
        self._user_id = id_


class TreeUser(BaseTreeUser):

    def __init__(self, name: str = '', email: str = '', description: str = '', **kwargs):
        super().__init__(name=name, **kwargs)
        self.socket_id: bytes = b''
        self.email: str = email
        self.description: str = description

    @property
    def id_(self) -> bytes:
        if self._user_id:
            return self._user_id
        else:
            return self.socket_id

    def to_dict(self) -> dict:
        d = {}
        for k, v in super().to_dict().items():
            if k == 'socket_id':
                continue
            d[k] = v
        return d


class TreeServiceUser(BaseTreeUser):
    def __init__(self, id_: uuid = None, name: str = '', **kwargs):
        super().__init__(name=name, **kwargs)
        self._user_id = id_ if id_ else uuid.uuid1().bytes
