"""Pydantic v2 canonical input and output schemas for Bodhan MCP tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SchemaBase(BaseModel):
    """Base model with standard configuration."""
    model_config = ConfigDict(extra="forbid")


# --- Audio Transcription Schemas ---

class Segment(SchemaBase):
    """Timestamped transcript segment."""
    start_s: float
    end_s: float
    text: str


class TranscribeIn(SchemaBase):
    """Input parameters for transcribe_audio tool."""
    input_path: str
    language_hint: str | None = None


class TranscribeOut(SchemaBase):
    """Output payload for transcribe_audio tool."""
    transcript: str
    language: str
    language_confidence: float
    segments: list[Segment]
    duration_s: float
    cost_inr: float


# --- Text Translation Schemas ---

class TranslateIn(SchemaBase):
    """Input parameters for translate_text tool."""
    text: str
    source_lang: str
    target_lang: str


class TranslateOut(SchemaBase):
    """Output payload for translate_text tool."""
    translated: str
    cost_inr: float


# --- Speech Synthesis Schemas ---

class SpeakIn(SchemaBase):
    """Input parameters for synthesize_speech tool."""
    text: str
    language: str
    voice_id: str


class SpeakOut(SchemaBase):
    """Output payload for synthesize_speech tool."""
    path: str
    duration_s: float
    chars: int
    ai_generated: bool = True
    cost_inr: float


# --- OCR Schemas ---

class OcrPageIn(SchemaBase):
    """Input parameters for ocr_page tool."""
    image_path: str


class OcrPageOut(SchemaBase):
    """Output payload for ocr_page tool."""
    markdown: str
    page: int = 1
    cost_inr: float


class OcrDocIn(SchemaBase):
    """Input parameters for ocr_document tool."""
    pdf_path: str


class OcrDocOut(SchemaBase):
    """Output payload for ocr_document tool."""
    markdown: str
    pages: int
    cost_inr: float


# --- Media Inspection Schemas ---

MediaKind = Literal["audio", "image", "pdf", "text", "video", "unknown"]


class InspectIn(SchemaBase):
    """Input parameters for inspect_media tool."""
    path: str


class InspectOut(SchemaBase):
    """Output payload for inspect_media tool."""
    kind: MediaKind
    duration_s: float | None = None
    pages: int | None = None
    format: str
    size_mb: float


# --- Helper Tool Schemas (Standing Rule F5) ---

class AudioChunk(SchemaBase):
    """Audio slice definition with time boundaries."""
    path: str
    start_s: float
    end_s: float


class ChunkAudioIn(SchemaBase):
    """Input parameters for chunk_audio tool."""
    path: str


class ChunkAudioOut(SchemaBase):
    """Output payload for chunk_audio tool."""
    chunks: list[AudioChunk]


class VoiceLibraryOut(SchemaBase):
    """Output payload for get_voice_library tool."""
    voices: dict[str, list[str]] = Field(default_factory=dict)
