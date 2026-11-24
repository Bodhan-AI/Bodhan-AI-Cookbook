"""Typed, safe failures surfaced to agents and external MCP clients."""


class TaskError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "retryable": self.retryable}
