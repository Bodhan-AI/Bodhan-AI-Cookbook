"""Data models and schemas for Bodhan MCP tools and agent."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MediaInfo(BaseModel):
    """Metadata describing an inspected media file."""
    path: str
    media_type: str = Field(description="audio | video | image | pdf | text | unknown")
    size_bytes: int
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    page_count: Optional[int] = None
    format: Optional[str] = None
    notes: Optional[str] = None


class LanguageInfo(BaseModel):
    """Detected language/script metadata."""
    code: str = Field(description="ISO 639-1 / Bodhan language code, e.g. 'hi', 'ta', 'en'")
    name: str = Field(description="Display name, e.g. 'Hindi', 'Tamil'")
    script: str = Field(description="Detected script, e.g. 'Devanagari', 'Tamil', 'Latin'")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    is_indic: bool = True
    is_supported: bool = True


class VoiceInfo(BaseModel):
    """Voice metadata for Indic-Speak."""
    name: str
    language: str
    gender: str = Field(description="'female' or 'male'")
    styles: List[str] = Field(default_factory=list)


class TranscribeResult(BaseModel):
    """Result of speech-to-text transcription."""
    text: str
    language: Optional[str] = None
    duration_seconds: float = 0.0
    chunk_count: int = 1
    srt_subtitles: Optional[str] = None
    cost_inr: float = 0.0
    cached: bool = False


class TranslateResult(BaseModel):
    """Result of text translation."""
    translation: str
    source_language: Optional[str] = None
    target_language: str
    script: str = "native"
    output_tokens: int = 0
    cost_inr: float = 0.0
    cached: bool = False
    truncated: bool = False


class SpeakResult(BaseModel):
    """Result of text-to-speech synthesis."""
    audio_path: str = Field(description="Absolute or relative file path to the generated WAV audio file")
    voice: str
    language: str
    style: Optional[str] = None
    character_count: int = 0
    duration_seconds: float = 0.0
    cost_inr: float = 0.0
    cached: bool = False


class OCRBlock(BaseModel):
    """Single layout block from OCR."""
    order: int
    label: str
    bbox_xyxy: List[float]
    conf: float
    text: str


class OCRResult(BaseModel):
    """Result of document OCR."""
    markdown: str
    page_count: int = 1
    blocks: List[OCRBlock] = Field(default_factory=list)
    cost_inr: float = 0.0
    cached: bool = False


class ToolError(BaseModel):
    """Structured actionable error description."""
    error: str
    code: str
    suggestion: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
