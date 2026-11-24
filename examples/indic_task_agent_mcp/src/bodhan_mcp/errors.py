"""Structured, actionable tool errors.

Every failure a caller can act on becomes a ToolError with a stable `code`, a plain message, whether a retry
can help, and a hint naming the fix. The server turns it into an MCP error result whose text is JSON, so any
client (or LLM) can branch on `code` instead of parsing prose.
"""

from __future__ import annotations

import json
from typing import Any

CODES = {
    "invalid_argument", "file_not_found", "path_not_allowed", "unsupported_format", "unsupported_language",
    "unknown_voice", "input_too_long", "empty_input", "missing_api_key", "rate_limited", "upstream_error",
    "upstream_timeout", "budget_exceeded", "verification_failed", "internal_error",
}


class ToolError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, hint: str = "", details: dict[str, Any] | None = None):
        assert code in CODES, code
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.hint = hint
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            body["hint"] = self.hint
        if self.details:
            body["details"] = self.details
        return {"error": body}

    def __str__(self) -> str:  # what MCP clients see as the error text
        return json.dumps(self.as_dict(), ensure_ascii=False)
