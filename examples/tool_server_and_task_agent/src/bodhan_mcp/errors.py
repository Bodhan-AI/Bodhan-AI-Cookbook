"""Structured errors for the Bodhan tool server and task agent.

Every failure a tool can report carries a stable symbolic ``code`` so
callers (the agent, an MCP client, a human reading a trace) can branch on
it instead of parsing message text. Tool functions raise :class:`ToolError`;
the MCP layer converts it into a JSON error object and never crashes.
"""

from __future__ import annotations


class ToolError(Exception):
    """A failure with a stable code, a human-readable message and a hint.

    Attributes:
        code: stable symbolic code (e.g. ``unsupported_language``).
        message: what happened, in plain words.
        hint: what the caller should do instead (may be empty).
        retryable: True when retrying the same call could succeed.
        status: HTTP-style status when the failure came from an API call.
    """

    def __init__(
        self,
        code: str,
        message: str,
        hint: str = "",
        retryable: bool = False,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.status = status

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            out["hint"] = self.hint
        if self.status is not None:
            out["status"] = self.status
        return out


# Stable codes used across tools, the client and the agent.
UNSUPPORTED_LANGUAGE = "unsupported_language"
FILE_NOT_FOUND = "file_not_found"
UNSUPPORTED_MEDIA = "unsupported_media"
INVALID_INPUT = "invalid_input"
AUTH_ERROR = "auth_error"
RATE_LIMITED = "rate_limited"
UPSTREAM_ERROR = "upstream_error"
BUDGET_EXCEEDED = "budget_exceeded"
TOOL_TIMEOUT = "tool_timeout"
EMPTY_RESULT = "empty_result"
MISSING_KEY = "missing_api_key"
