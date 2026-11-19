"""Structured, actionable tool errors.

Every failure a caller can see is a ToolError with a stable `code`, a plain
message, a `hint` that says what to do next, and whether retrying could help.
"""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    def __init__(self, code: str, message: str, *, hint: str = "", retryable: bool = False, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


# Codes callers can branch on. Keep in sync with docs/design.md.
INVALID_ARGUMENT = "invalid_argument"
FILE_NOT_FOUND = "file_not_found"
PATH_NOT_ALLOWED = "path_not_allowed"
UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
UNSUPPORTED_LANGUAGE = "unsupported_language"
UNSUPPORTED_VOICE = "unsupported_voice"
BUDGET_EXCEEDED = "budget_exceeded"
MISSING_CREDENTIALS = "missing_credentials"
AUTH_FAILED = "auth_failed"
CREDIT_EXHAUSTED = "credit_exhausted"
RATE_LIMITED = "rate_limited"
UPSTREAM_TIMEOUT = "upstream_timeout"
UPSTREAM_UNAVAILABLE = "upstream_unavailable"
UPSTREAM_REJECTED = "upstream_rejected"
TRUNCATED_OUTPUT = "truncated_output"
EMPTY_RESULT = "empty_result"
MEDIA_ERROR = "media_error"
PROVENANCE_VIOLATION = "provenance_violation"
INTERNAL_ERROR = "internal_error"


# What each code means for a caller, served as the bodhan://errors resource.
CATALOGUE: dict[str, tuple[bool, str]] = {
    INVALID_ARGUMENT: (False, "An argument is missing, of the wrong type or out of range. Fix the call using `details.problems` and the tool's schema."),
    FILE_NOT_FOUND: (False, "The path does not exist. Use a path returned by an earlier tool or an input file."),
    PATH_NOT_ALLOWED: (False, "The path is outside the folders this server may read (--allow-dir)."),
    UNSUPPORTED_FILE_TYPE: (False, "The file is not a kind this tool reads; `hint` names the tool that does."),
    UNSUPPORTED_LANGUAGE: (False, "The language is not supported by the Bodhan model. Report it; do not substitute another language."),
    UNSUPPORTED_VOICE: (False, "Unknown voice for this language. `details.voices_for_language` lists the valid ones (or read bodhan://voices)."),
    BUDGET_EXCEEDED: (False, "The call would exceed the spending cap. Stop or choose a cheaper step."),
    MISSING_CREDENTIALS: (False, "The key for this model is not set (BODHAN_*_KEY)."),
    AUTH_FAILED: (False, "The key was rejected by Bodhan."),
    CREDIT_EXHAUSTED: (False, "The Bodhan account has no credit left. No retry will help."),
    RATE_LIMITED: (True, "Bodhan is rate limiting; the tool already retried with backoff. Retry later or slow down."),
    UPSTREAM_TIMEOUT: (True, "The model did not answer in time. The request may have been billed (counted as uncertain)."),
    UPSTREAM_UNAVAILABLE: (True, "Bodhan returned a server error or an unreadable reply. Retry once, then report."),
    UPSTREAM_REJECTED: (False, "Bodhan refused the request (4xx). `message` has its reason."),
    TRUNCATED_OUTPUT: (True, "The model's output was cut off and could not be completed by splitting. The partial result is not delivered."),
    EMPTY_RESULT: (False, "The model returned nothing usable (e.g. no speech in the audio)."),
    MEDIA_ERROR: (False, "The media file could not be decoded."),
    PROVENANCE_VIOLATION: (False, "Indian-language text was written by the caller. Pass such text by path or write English and translate it."),
    INTERNAL_ERROR: (False, "A bug in this server. The server keeps running; the message says what failed."),
}
