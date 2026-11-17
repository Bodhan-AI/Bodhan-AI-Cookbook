"""Error codes, BodhanError exception, and response helpers."""

from __future__ import annotations

import enum
from typing import Any


class ErrorCode(enum.Enum):
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    UPSTREAM_RATE_LIMIT = "UPSTREAM_RATE_LIMIT"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    PATH_OUTSIDE_WORKSPACE = "PATH_OUTSIDE_WORKSPACE"
    OUTPUT_FAILED_VERIFICATION = "OUTPUT_FAILED_VERIFICATION"


class BodhanError(Exception):
    """Raised when a Bodhan tool encounters a known error condition."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        how_to_fix: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.how_to_fix = how_to_fix
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "how_to_fix": self.how_to_fix,
            "retryable": self.retryable,
        }


def error_response(
    code: ErrorCode,
    message: str,
    how_to_fix: str = "",
    retryable: bool = False,
) -> dict[str, Any]:
    """Return a standard error response dict."""
    return {
        "ok": False,
        "error": {
            "code": code.value,
            "message": message,
            "how_to_fix": how_to_fix,
            "retryable": retryable,
        },
    }


def success_response(
    data: Any,
    warnings: list[str] | None = None,
    cost_inr: float = 0.0,
    cached: bool = False,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Return a standard success response dict."""
    return {
        "ok": True,
        "data": data,
        "warnings": warnings if warnings is not None else [],
        "cost_inr": cost_inr,
        "cached": cached,
        "duration_ms": duration_ms,
    }
