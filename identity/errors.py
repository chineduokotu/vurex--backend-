class IdentityError(Exception):
    def __init__(self, code, message, status=400, retry_after=None):
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after
        super().__init__(message)
