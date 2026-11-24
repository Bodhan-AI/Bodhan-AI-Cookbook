"""Typed errors with actionable messages.

Every error surfaced to the user should say what failed, why, and what to do
next. A traceback alone is not an acceptable user-facing failure.
"""

from __future__ import annotations


class BodhanDubError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(BodhanDubError):
    """Missing or invalid configuration, such as an absent API key."""


class MediaError(BodhanDubError):
    """The input could not be probed or decoded."""


class StageNotImplemented(BodhanDubError):
    """A pipeline stage has not been built yet."""

    def __init__(self, stage: str) -> None:
        super().__init__(
            f"stage {stage!r} is not implemented yet. "
            "Run with --stage asr to stop after transcription."
        )
        self.stage = stage


class BodhanAPIError(BodhanDubError):
    """A Bodhan model call failed."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        detail = f"{status} {code}: {message}"
        if request_id:
            detail += f" (request_id={request_id})"
        super().__init__(detail)
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retryable = retryable
