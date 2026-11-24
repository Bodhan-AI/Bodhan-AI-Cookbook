"""Structured, actionable tool errors.

Every failure a tool can anticipate is raised as ``ToolFailure``. The server turns it into an
MCP ``is_error`` result whose text is a JSON object:

    {"error": {"code": "unsupported_language", "message": "...", "hint": "...",
               "retryable": false, "details": {...}}}

``code`` is a stable symbolic string that callers can branch on; ``hint`` says what to do next.
"""

from __future__ import annotations

import json
from typing import Any

# Stable error codes. Keep in sync with docs/tool_reference.md.
INVALID_INPUT = "invalid_input"
FILE_NOT_FOUND = "file_not_found"
PATH_NOT_ALLOWED = "path_not_allowed"
UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
UNSUPPORTED_LANGUAGE = "unsupported_language"
UNSUPPORTED_VOICE = "unsupported_voice"
LANGUAGE_MISMATCH = "language_mismatch"
INPUT_TOO_LARGE = "input_too_large"
NO_SPEECH = "no_speech_detected"
BUDGET_EXCEEDED = "budget_exceeded"
MISSING_API_KEY = "missing_api_key"
AUTH_FAILED = "auth_failed"
RATE_LIMITED = "rate_limited"
UPSTREAM_TIMEOUT = "upstream_timeout"
UPSTREAM_ERROR = "upstream_error"
FFMPEG_MISSING = "ffmpeg_missing"
MEDIA_DECODE_FAILED = "media_decode_failed"
INTERNAL_ERROR = "internal_error"


class ToolFailure(Exception):
    """An anticipated failure with a machine-readable code and a next step for the caller."""

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
        body: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            body["hint"] = self.hint
        if self.details:
            body["details"] = self.details
        return {"error": body}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def parse_error_text(text: str) -> dict[str, Any] | None:
    """Recover the error object from an MCP error result's text (the SDK may prefix it)."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return data["error"]
    return None
