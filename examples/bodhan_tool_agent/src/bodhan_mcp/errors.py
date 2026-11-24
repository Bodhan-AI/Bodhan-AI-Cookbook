"""Error codes and structured error payload definition for Bodhan MCP tools."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --- Standard error codes per SPEC §6.3 ---
FILE_NOT_FOUND = "FILE_NOT_FOUND"
UNREADABLE_MEDIA = "UNREADABLE_MEDIA"
UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
INPUT_TOO_LONG = "INPUT_TOO_LONG"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
INTERNAL = "INTERNAL"

KNOWN_ERROR_CODES = {
    FILE_NOT_FOUND,
    UNREADABLE_MEDIA,
    UNSUPPORTED_LANGUAGE,
    INPUT_TOO_LONG,
    INVALID_ARGUMENT,
    UPSTREAM_RATE_LIMITED,
    UPSTREAM_TIMEOUT,
    BUDGET_EXCEEDED,
    INTERNAL,
}


class ToolErrorPayload(BaseModel):
    """Structured error payload returned across the MCP boundary instead of raising exceptions."""
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    hint: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionError(Exception):
    """Internal exception that encapsulates a ToolErrorPayload before crossing MCP boundary."""

    def __init__(self, payload: ToolErrorPayload):
        super().__init__(payload.message)
        self.payload = payload


def make_tool_error(
    code: str,
    message: str,
    hint: str,
    details: dict[str, Any] | None = None,
) -> ToolErrorPayload:
    """Helper to construct a validated ToolErrorPayload."""
    return ToolErrorPayload(
        code=code,
        message=message,
        hint=hint,
        details=details or {},
    )
