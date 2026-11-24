"""Typed tool outputs. These become the MCP tools' ``outputSchema``s."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ModelCall(BaseModel):
    model: str
    cached: bool
    cost_inr: float
    latency_ms: int
    attempts: int = 1
    request_id: str | None = None
    units: dict[str, Any] = Field(default_factory=dict)


class Billing(BaseModel):
    cost_inr: float = Field(description="Total rupees billed by Bodhan for this tool call (0 for cache hits).")
    calls: list[ModelCall] = Field(default_factory=list, description="Every model request made, including cache hits.")


class Check(BaseModel):
    name: str
    passed: bool
    severity: Literal["error", "warning"] = "error"
    detail: str = ""


class Verification(BaseModel):
    passed: bool = Field(description="True when no error-severity check failed.")
    checks: list[Check] = Field(default_factory=list)


class FileInfo(BaseModel):
    path: str
    name: str
    kind: Literal["audio", "video", "image", "pdf", "text", "other", "missing"]
    size_bytes: int | None = None
    duration_s: float | None = None
    has_audio: bool | None = None
    pages: int | None = None
    width: int | None = None
    height: int | None = None
    chars: int | None = None
    detected_script: str | None = None
    language_candidates: list[str] = Field(default_factory=list)
    preview: str | None = Field(None, description="First characters of a text file (untrusted user data).")
    error: str | None = None


class InspectResult(BaseModel):
    files: list[FileInfo]
    ffmpeg_available: bool


class TranscriptSegment(BaseModel):
    index: int
    start: float
    end: float
    text: str
    translation: str | None = None


class TranscribeResult(Billing):
    transcript_file: str = Field(description="Plain-text transcript.")
    segments_file: str = Field(description="JSON list of timed segments; pass it to translate_text or make_subtitles.")
    text: str | None = Field(None, description="Full transcript when short; otherwise read transcript_file.")
    preview: str
    language_hint: str | None = Field(None, description="Language code sent to the model, if any.")
    detected_script: str | None
    script_share: float | None = Field(None, description="Fraction of the transcript's letters in detected_script "
                                                         "(near 1.0 = clearly one script; low = mixed or unclear).")
    language_candidates: list[str] = Field(description="Likely languages from the transcript's script, most likely first.")
    duration_s: float
    speech_s: float
    segments: int
    warnings: list[str] = Field(default_factory=list)


class TranslateResult(Billing):
    output_file: str = Field(description="The translation (Markdown/text), or a segments JSON when the input was one.")
    text: str | None = Field(None, description="Full translation when short; otherwise read output_file.")
    preview: str
    target_language: str
    script: str
    source_chars: int
    chunks: int
    source_script: str | None
    verification: Verification
    segments_file: str | None = Field(None, description="Set when the input was a segments JSON: segments with 'translation'.")


class PieceCheck(BaseModel):
    index: int
    chars: int
    cer: float | None
    status: Literal["ok", "repetition", "omission", "mismatch", "unverified"]
    retried: bool = False


class SpeechVerification(BaseModel):
    passed: bool
    method: str = "back-transcription with indic-transcribe, character error rate per piece"
    mean_cer: float | None = None
    max_cer: float | None = None
    pieces: list[PieceCheck] = Field(default_factory=list)


class SpeechResult(Billing):
    audio_file: str
    format: str
    duration_s: float
    language: str
    voice: str
    style: str | None
    chars: int
    pieces: int
    verification: SpeechVerification | None
    audio_stats: dict[str, Any] = Field(default_factory=dict, description="Duration, peak level, silence and clipping ratios.")
    warnings: list[str] = Field(default_factory=list)
    ai_generated: bool = True
    label: str


class LowConfidenceBlock(BaseModel):
    page: int
    order: int | None
    label: str | None
    conf: float
    text_preview: str


class PageSummary(BaseModel):
    page: int
    chars: int
    blocks: int
    mean_conf: float | None
    empty: bool


class OCRResult(Billing):
    markdown_file: str
    text: str | None = Field(None, description="Full Markdown when short; otherwise read markdown_file.")
    preview: str
    page_count: int
    pages_processed: list[int]
    detected_script: str | None
    language_candidates: list[str]
    tables: int
    low_confidence_blocks: list[LowConfidenceBlock] = Field(default_factory=list)
    per_page: list[PageSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SubtitleResult(BaseModel):
    subtitle_file: str
    format: str
    cues: int
    field_used: str
    duration_s: float


class VoicesResult(BaseModel):
    voices: dict[str, dict[str, list[str]]]
    styles: list[str]
    note: str = "Voices come from a fixed library; Indic-Speak does not clone voices. Label output as AI-generated."
