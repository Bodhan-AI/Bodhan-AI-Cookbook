"""Structured tool errors. Tools never raise to the MCP client; they return these."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorInfo(BaseModel):
    error_code: str = Field(description="Stable machine-readable code, e.g. FILE_NOT_FOUND, UNSUPPORTED_LANGUAGE.")
    message: str = Field(description="What went wrong, in plain English.")
    retryable: bool = Field(description="True if the same call may succeed if repeated later (rate limit, timeout).")
    hint: str = Field(description="What the caller should do next.")


class ToolFailure(Exception):
    """Raised inside tool code; converted to ErrorInfo at the tool boundary."""

    def __init__(self, error_code: str, message: str, *, retryable: bool = False, hint: str = ""):
        super().__init__(message)
        self.info = ErrorInfo(error_code=error_code, message=message, retryable=retryable, hint=hint)
