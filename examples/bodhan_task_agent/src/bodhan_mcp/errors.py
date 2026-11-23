"""Structured, actionable errors shared by the client, the tools and the agent.

Every failure a caller can see carries a stable machine-readable ``code``, a human
``message``, a ``hint`` saying what to do next, and whether retrying the same call
could succeed. MCP clients receive this as JSON inside an ``is_error`` tool result.
"""

from __future__ import annotations

import json
from typing import Any


class BodhanError(Exception):
    """An anticipated failure with a stable error code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out

    def to_json(self) -> str:
        return json.dumps({"error": self.to_dict()}, ensure_ascii=False)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (hint: {self.hint})" if self.hint else "")


# Stable error codes. Keep these in sync with docs/design_notes.md.
INVALID_INPUT = "invalid_input"
FILE_NOT_FOUND = "file_not_found"
UNSUPPORTED_MEDIA = "unsupported_media"
UNSUPPORTED_LANGUAGE = "unsupported_language"
UNKNOWN_VOICE = "unknown_voice"
MISSING_API_KEY = "missing_api_key"
AUTH_FAILED = "auth_failed"
RATE_LIMITED = "rate_limited"
UPSTREAM_TIMEOUT = "upstream_timeout"
UPSTREAM_ERROR = "upstream_error"
BUDGET_EXCEEDED = "budget_exceeded"
PATH_NOT_ALLOWED = "path_not_allowed"
MEDIA_TOOL_MISSING = "media_tool_missing"
EMPTY_RESULT = "empty_result"
