"""Structured, actionable tool errors.

Every failure a caller can see is a ToolError with a stable `code`, a plain
message, a `hint` that says what to do next, and whether retrying could help.
"""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    def __init__(self, code: str, message: str, *, hint: str = "", retryable: bool = False, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


# Codes callers can branch on. Keep in sync with docs/design.md.
INVALID_ARGUMENT = "invalid_argument"
FILE_NOT_FOUND = "file_not_found"
PATH_NOT_ALLOWED = "path_not_allowed"
UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
UNSUPPORTED_LANGUAGE = "unsupported_language"
UNSUPPORTED_VOICE = "unsupported_voice"
BUDGET_EXCEEDED = "budget_exceeded"
MISSING_CREDENTIALS = "missing_credentials"
AUTH_FAILED = "auth_failed"
CREDIT_EXHAUSTED = "credit_exhausted"
RATE_LIMITED = "rate_limited"
UPSTREAM_TIMEOUT = "upstream_timeout"
UPSTREAM_UNAVAILABLE = "upstream_unavailable"
UPSTREAM_REJECTED = "upstream_rejected"
TRUNCATED_OUTPUT = "truncated_output"
EMPTY_RESULT = "empty_result"
MEDIA_ERROR = "media_error"
PROVENANCE_VIOLATION = "provenance_violation"
INTERNAL_ERROR = "internal_error"
