"""Structured, actionable tool errors.

Every anticipated failure is raised as `BodhanToolError`. MCP turns it into a result with
`isError: true` whose text is a JSON object:

    {"error": {"code": "unsupported_language", "message": "...", "hint": "...", "retryable": false, ...}}

`code` is a stable machine-readable value (listed in ERROR_CODES) so clients can branch on it;
`hint` tells the caller what to do next. Unexpected crashes are caught by the SDK and never
take the server down.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

ERROR_CODES = {
    "invalid_input": "An argument is missing, malformed or out of range.",
    "file_not_found": "A path does not exist or is not a regular file.",
    "unsupported_file_type": "The file type cannot be handled by this tool.",
    "no_audio_stream": "The media file has no audio track.",
    "unsupported_language": "The language is not supported by the Bodhan model behind this tool.",
    "script_mismatch": "The text is not written in the script of the requested language.",
    "unsupported_voice": "The voice or style is not in Bodhan's voice library.",
    "budget_exceeded": "The call would cost more than max_cost_inr allows.",
    "missing_api_key": "No Bodhan API key is configured for this model.",
    "auth_error": "Bodhan rejected the API key (401).",
    "payload_too_large": "Bodhan rejected the request as too large (413).",
    "rate_limited": "Bodhan kept returning 429 after retries.",
    "timeout": "Bodhan did not answer in time after retries.",
    "upstream_error": "Bodhan returned a server error after retries.",
    "upstream_rejected": "Bodhan rejected the request (4xx); see message.",
    "partial_failure": "Some items failed; see details. Successful items are cached.",
}


class BodhanToolError(ToolError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}
        payload: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
        if hint:
            payload["hint"] = hint
        if details:
            payload["details"] = details
        super().__init__(json.dumps({"error": payload}, ensure_ascii=False, default=str))

    def with_details(self, **extra: Any) -> BodhanToolError:
        return BodhanToolError(
            self.code, self.message, hint=self.hint, retryable=self.retryable, details={**self.details, **extra}
        )


def parse_error_text(text: str) -> dict[str, Any] | None:
    """Pull the {"error": {...}} object back out of an MCP error result (clients prefix their own text)."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    return obj.get("error") if isinstance(obj, dict) else None
