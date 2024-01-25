class BaseCommunicationError(Exception):

    def __init__(self, message='', **kwargs):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        if self.message:
            return self.message
        return super().__str__()


class CommunicationTimeoutError(BaseCommunicationError):

    def __init__(self, message='The response did not come before timeout', **kwargs):
        super().__init__(message=message, **kwargs)


class CommunicationRuntimeError(BaseCommunicationError):

    def __init__(self, message='', **kwargs):
        super().__init__(message=message, **kwargs)
