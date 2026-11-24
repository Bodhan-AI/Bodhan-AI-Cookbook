"""Structured, actionable tool errors.

Every tool failure is a ToolError with a stable `code`, a human message and a
`hint` telling the caller what to do next. The MCP server and the agent both
turn these into JSON instead of crashing.
"""

from __future__ import annotations


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str = "", retryable: bool = False, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details

    def to_dict(self) -> dict:
        out = {"ok": False, "error": {"code": self.code, "message": self.message, "retryable": self.retryable}}
        if self.hint:
            out["error"]["hint"] = self.hint
        if self.details:
            out["error"]["details"] = self.details
        return out


# Stable error codes (documented in docs/design.md).
INVALID_INPUT = "invalid_input"
FILE_NOT_FOUND = "file_not_found"
UNSUPPORTED_LANGUAGE = "unsupported_language"
UNSUPPORTED_MEDIA = "unsupported_media"
MISSING_KEY = "missing_api_key"
RATE_LIMITED = "rate_limited"
UPSTREAM = "upstream_error"
TIMEOUT = "timeout"
BUDGET = "budget_exceeded"
POLICY = "policy_violation"
