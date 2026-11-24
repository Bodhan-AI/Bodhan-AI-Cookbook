"""One error type for every tool, so callers always get the same shape."""

from __future__ import annotations


class ToolError(Exception):
    """A failure the caller can act on.

    code      short machine-readable label, e.g. "unsupported_language"
    message   what went wrong, in plain words
    hint      what the caller should do next
    retryable True when calling again later with the same input may succeed
    """

    def __init__(self, code: str, message: str, hint: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable

    def to_dict(self) -> dict:
        err = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            err["hint"] = self.hint
        return {"ok": False, "error": err}
