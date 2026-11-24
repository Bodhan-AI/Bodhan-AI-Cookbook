"""Typed Pydantic schemas for Bodhan MCP tool inputs, outputs, and structured errors."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Machine-readable error code, e.g. 'file_not_found', 'unsupported_language'.")
    message: str = Field(..., description="Human-readable explanation of what went wrong.")
    retryable: bool = Field(default=False, description="Whether retrying the same request might succeed.")
    hint: str | None = Field(default=None, description="Actionable suggestion for how to fix or recover.")


class ToolResponse(BaseModel):
    ok: bool
    error: ErrorDetail | None = None
    cost_inr: float = 0.0
    cache_hit: bool = False
    model: str | None = None
    untrusted: bool = False
    preview: str | None = Field(
        default=None,
        description="Bounded preview (<=300 chars) for third-party MCP clients. Marked untrusted.",
    )
    data: dict[str, Any] = Field(default_factory=dict)


def ok_response(
    data: dict[str, Any],
    *,
    cost_inr: float = 0.0,
    cache_hit: bool = False,
    model: str | None = None,
    preview: str | None = None,
    untrusted: bool = False,
) -> dict[str, Any]:
    if preview is not None and len(preview) > 300:
        preview = preview[:297] + "..."
    return ToolResponse(
        ok=True,
        error=None,
        cost_inr=round(cost_inr, 6),
        cache_hit=cache_hit,
        model=model,
        untrusted=untrusted,
        preview=preview,
        data=data,
    ).model_dump()


def err_response(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    hint: str | None = None,
    cost_inr: float = 0.0,
    model: str | None = None,
) -> dict[str, Any]:
    return ToolResponse(
        ok=False,
        error=ErrorDetail(code=code, message=message, retryable=retryable, hint=hint),
        cost_inr=round(cost_inr, 6),
        cache_hit=False,
        model=model,
        untrusted=False,
        preview=None,
        data={},
    ).model_dump()


# Structured sub-models used across tools and agent intake.
FileKind = Literal["audio", "video", "image", "pdf", "text", "missing", "unknown"]


class FileFact(BaseModel):
    path: str
    exists: bool
    kind: FileKind
    mime_type: str | None = None
    size_bytes: int = 0
    duration_s: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    page_count: int | None = None
    image_width: int | None = None
    image_height: int | None = None
    char_count: int | None = None
    script_histogram: dict[str, float] = Field(default_factory=dict)
    detected_language_hint: str | None = None
    estimated_cost_inr: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


class LanguageCandidate(BaseModel):
    language: str
    name: str
    confidence: float
    script: str | None = None


class TranscriptSegment(BaseModel):
    index: int
    start_s: float
    end_s: float
    text_path: str
    char_count: int
    duration_s: float
    dropped_silence: bool = False


class SpeechChunkInfo(BaseModel):
    index: int
    audio_path: str
    text_path: str
    char_count: int
    duration_s: float


class ChunkVerificationVerdict(BaseModel):
    index: int
    audio_path: str
    expected_chars: int
    transcribed_chars: int
    cer: float
    repetition_detected: bool
    repeated_ngram: str | None = None
    duration_s: float
    chars_per_second: float
    duration_anomaly: bool
    passed: bool
    reasons: list[str] = Field(default_factory=list)
