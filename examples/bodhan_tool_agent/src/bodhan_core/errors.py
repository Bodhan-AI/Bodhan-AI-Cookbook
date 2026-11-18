"""Structured, actionable errors shared by every tool.

Tools never raise to the MCP client: they return ``{"ok": false, "error": {...}}`` built from a ToolError.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    MISSING_API_KEY = "MISSING_API_KEY"
    AUTH_FAILED = "AUTH_FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"  # this run's --max-cost-inr would be exceeded
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"  # the Bodhan account is out of credit
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL = "INTERNAL"


RETRYABLE = {ErrorCode.RATE_LIMITED, ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_ERROR}


class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str
    hint: str | None = None
    retryable: bool = False


class ToolError(Exception):
    def __init__(self, code: ErrorCode, message: str, hint: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.info = ErrorInfo(
            code=code,
            message=message,
            hint=hint,
            retryable=code in RETRYABLE if retryable is None else retryable,
        )

    def to_result(self) -> dict:
        return {"ok": False, "error": self.info.model_dump(mode="json")}
