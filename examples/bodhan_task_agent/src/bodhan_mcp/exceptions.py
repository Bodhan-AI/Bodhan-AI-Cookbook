from __future__ import annotations

import json
from typing import Any, Literal

ErrorCode = Literal[
    "invalid_input",
    "file_not_found",
    "file_exists",
    "path_not_allowed",
    "unsupported_file",
    "unsupported_encoding",
    "unsupported_language",
    "input_too_large",
    "missing_api_key",
    "auth_failed",
    "insufficient_credit",
    "rate_limited",
    "timeout",
    "tool_deadline_exceeded",
    "incomplete_result",
    "upstream_error",
    "internal_error",
]

STATUS: dict[str, int] = {
    "invalid_input": 400,
    "missing_api_key": 401,
    "auth_failed": 401,
    "insufficient_credit": 402,
    "path_not_allowed": 403,
    "file_not_found": 404,
    "file_exists": 409,
    "input_too_large": 413,
    "unsupported_file": 415,
    "unsupported_encoding": 415,
    "unsupported_language": 422,
    "rate_limited": 429,
    "internal_error": 500,
    "upstream_error": 502,
    "incomplete_result": 502,
    "timeout": 504,
    "tool_deadline_exceeded": 504,
}


class BodhanToolError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        hint: str = "",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = STATUS[code]
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "status": self.status,
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "retryable": self.retryable,
                "details": self.details,
            }
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
