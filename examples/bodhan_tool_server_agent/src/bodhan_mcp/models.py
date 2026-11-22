"""Typed tool outputs (become MCP outputSchema). Every paid tool also reports cost, cache use and warnings."""
from __future__ import annotations

from pydantic import BaseModel, Field


class _Base(BaseModel):
    request_ids: list[str] = Field(default_factory=list, description="Bodhan x-request-id values for this call, for support/tracing")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal problems the caller should read")


class InspectOut(BaseModel):
    path: str
    bytes: int
    kind: str = Field(description="audio | video | image | pdf | text | unknown")
    notes: list[str] = []
    duration_s: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None
    pages: int | None = None
    width: int | None = None
    height: int | None = None
    chars: int | None = None
    text_encoding: str | None = None
    dominant_script: str | None = None


class TranscribeOut(_Base):
    language: str | None = Field(description="Detected or given language code")
    language_confidence: float
    language_method: str
    language_alternatives: list[str] = []
    text_ref: str = Field(description="Path of the full transcript (UTF-8 text)")
    segments_ref: str = Field(description="Path of segments JSON [{start,end,text}]; timings are approximate")
    preview: str
    duration_s: float
    chunks: int
    cached_chunks: int
    cost_inr: float


class TranslateOut(_Base):
    translation_ref: str
    preview: str
    translation: str | None = Field(None, description="Inline only when 400 characters or fewer")
    target_language: str
    script: str
    source_language_detected: str | None = None
    chars_in: int
    chars_out: int
    resplit_pieces: int
    cached_calls: int
    cost_inr: float


class SegmentsOut(_Base):
    segments_ref: str
    cues: int
    target_language: str
    cost_inr: float


class SpeakOut(_Base):
    audio_ref: str
    format: str
    duration_s: float
    voice: str
    language: str
    style: str | None = None
    chunks: int
    chars: int
    chunk_map_ref: str = Field(description="JSON [{i,start,text}] to locate a bad chunk; use with redo_chunks")
    ai_generated: bool = True
    cached_chunks: int
    cost_inr: float


class OcrOut(_Base):
    markdown_ref: str
    preview: str
    pages_total: int
    pages_done: int
    pages_failed: list[dict] = []
    mean_confidence: float | None = None
    dominant_script: str | None = None
    chars: int
    cached_pages: int
    cost_inr: float


class SrtOut(BaseModel):
    srt_ref: str
    cues: int


class DetectOut(BaseModel):
    script: str | None
    language: str | None
    confidence: float
    alternatives: list[str]
    method: str


class CostOut(BaseModel):
    estimate_inr: float
    by_model: dict[str, float]
    spent_inr: float
    cap_inr: float | None


class TextFileOut(BaseModel):
    text: str
    chars: int
    truncated: bool
    offset: int = 0
    next_offset: int | None = None


class WriteOut(BaseModel):
    path: str
    chars: int
