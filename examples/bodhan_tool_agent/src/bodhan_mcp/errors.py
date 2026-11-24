"""Structured tool errors.

Every failure a caller can act on is a ToolFailure with a stable `code`, a
human-readable `message`, a `hint` saying what to do next, and whether simply
retrying later could help. Over MCP it travels as a ToolError whose text is the
JSON object, so clients can branch on `code` without parsing prose.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

ERROR_CODES = frozenset({
    "file_not_found",
    "unsupported_file_type",
    "unsupported_language",
    "invalid_argument",
    "budget_exceeded",
    "rate_limited",
    "upstream_error",
    "auth_error",
    "missing_api_key",
    "timeout",
    "model_output_truncated",
    "path_not_allowed",
    "internal_error",
})


class ToolFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        hint: str = "",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }
        if self.details:
            out["details"] = self.details
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_tool_error(self) -> ToolError:
        return ToolError(self.to_json())

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def parse_tool_error(text: str) -> dict[str, Any] | None:
    """Recover the error object from a tool error's text.

    The MCP server prefixes tool errors with "Error executing tool <name>: ",
    so the JSON starts at the first brace.
    """
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and "code" in obj else None
