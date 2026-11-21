class BodhanAPIError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.request_id = request_id


class BudgetExhausted(BodhanAPIError):
    pass
