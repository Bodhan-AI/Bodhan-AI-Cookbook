"""One error type for every Bodhan failure.

The four APIs report errors in different shapes. We convert all of them into BodhanError,
so the agent can branch on `code` and `retryable` and never has to parse message text.
"""

from __future__ import annotations

from typing import Any

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# Used when the API gives no usable symbolic code of its own.
_STATUS_CODES = {
    400: "invalid_request",
    401: "invalid_api_key",
    403: "forbidden",
    404: "not_found",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "invalid_request",
    429: "rate_limited",
}

# Short, actionable advice shown to the agent (and to the person reading the trace).
_HINTS = {
    "invalid_api_key": "Check the key for this model in .env. Each Bodhan key works for one model only.",
    "unsupported_media_type": "Convert the audio to 16-bit WAV before sending it.",
    "payload_too_large": "Split the input into smaller pieces and call again.",
    "rate_limited": "Wait and retry with backoff, and lower the number of parallel calls.",
    "unsupported_language": "Use the language-support tool to see which languages this model accepts.",
    "missing_api_key": "Set the key named in the message in .env or in the environment.",
}


class BodhanError(Exception):
    """A failed call to a Bodhan model (or a problem preparing one)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        model: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.model = model
        self.request_id = request_id
        self.retryable = retryable
        self.hint = hint or _HINTS.get(code)

    def to_dict(self) -> dict[str, Any]:
        """Structured form returned to the agent when a tool fails."""
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "status": self.status,
                "model": self.model,
                "request_id": self.request_id,
                "retryable": self.retryable,
                "hint": self.hint,
            },
        }

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    @classmethod
    def from_response(
        cls, model: str, status: int, body: Any, request_id: str | None = None
    ) -> BodhanError:
        """Build an error from an HTTP failure. `body` is the parsed JSON, or raw text."""
        message, raw_code = "", None
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):  # translate/OCR style: {"error": {"message", "code"/"type"}}
                message = str(err.get("message") or "")
                raw_code = err.get("code") or err.get("type")
                request_id = request_id or err.get("request_id")
            elif "detail" in body:  # gateway style: {"detail": "Not Found"}
                message = str(body["detail"])
        elif body:
            message = str(body)[:300]

        # Auth and rate-limit failures are named differently by each API
        # ("auth_error", "invalid_api_key_error", "rate_limit_exceeded_error"),
        # so we use one code for each. Otherwise keep the API's own symbolic code,
        # but not a bare number like "401".
        if status in (401, 429):
            code = _STATUS_CODES[status]
        elif isinstance(raw_code, str) and raw_code.replace("_", "").isalpha():
            code = raw_code
        elif status >= 500:
            code = "upstream_error"
        else:
            code = _STATUS_CODES.get(status, "http_error")

        return cls(
            code,
            message or f"HTTP {status}",
            status=status,
            model=model,
            request_id=request_id,
            retryable=status in RETRYABLE_STATUSES,
        )


class ConfigError(BodhanError):
    """A required setting (usually an API key) is missing."""

    def __init__(self, message: str, *, code: str = "missing_api_key") -> None:
        super().__init__(code, message, retryable=False)