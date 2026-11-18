"""Shared error types for bodhan_mcp."""
from __future__ import annotations
from dataclasses import dataclass, field

# ── Error codes ───────────────────────────────────────────────────────────────
INVALID_INPUT = "INVALID_INPUT"
FILE_NOT_FOUND = "FILE_NOT_FOUND"
UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
RATE_LIMITED = "RATE_LIMITED"
TIMEOUT = "TIMEOUT"
UPSTREAM_ERROR = "UPSTREAM_ERROR"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


@dataclass
class BodhanError(Exception):
    """Structured error returned from tools — never let raw tracebacks escape."""
    code: str
    message: str
    retryable: bool = False
    hint: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "hint": self.hint,
            },
        }

    def __str__(self) -> str:
        return f"BodhanError({self.code}): {self.message}"
