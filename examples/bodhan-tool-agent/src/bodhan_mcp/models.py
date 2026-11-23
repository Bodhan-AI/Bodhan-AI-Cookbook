"""Shared result and error types for the Bodhan MCP server."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


class ErrorCode(str, Enum):
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    AUTH_MISSING = "AUTH_MISSING"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    INTERNAL = "INTERNAL"


class ToolError(Exception):
    """A failure with enough structure for a caller to act on it."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        hint: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.hint = hint
        self.details = dict(details) if details else None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "hint": self.hint,
        }
        if self.details:
            out["details"] = self.details
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ToolError({self.code.value}, {self.message!r}, retryable={self.retryable})"


ArtifactKind = Literal[
    "audio",
    "text",
    "markdown",
    "subtitles",
    "transcript",
    "translation",
    "ocr",
    "json",
    "image",
    "other",
]


@dataclass(frozen=True)
class ArtifactRef:
    """A file a tool produced. `ref` can be passed back to any tool as `artifact:<ref>`."""

    ref: str
    path: str
    kind: ArtifactKind
    mime: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Usage:
    """What one tool call cost. `cached=True` means no upstream call was made (cost is 0)."""

    model: str
    cost_inr: float = 0.0
    latency_ms: int = 0
    cached: bool = False
    avoided_inr: float = 0.0
    units: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["units"] = dict(self.units)
        return d


def success(
    data: Mapping[str, Any],
    *,
    artifacts: Iterable[ArtifactRef] = (),
    usage: Usage | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """The success envelope. Keep `data` small: previews and counts, never whole documents."""
    return {
        "ok": True,
        "data": dict(data),
        "artifacts": [a.to_dict() for a in artifacts],
        "usage": usage.to_dict() if usage else None,
        "warnings": list(warnings),
    }


def failure(err: ToolError) -> dict[str, Any]:
    """The failure envelope."""
    return {"ok": False, "error": err.to_dict()}
