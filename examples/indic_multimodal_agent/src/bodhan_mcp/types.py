"""Pydantic data models and schemas for Bodhan MCP tools and errors."""

from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field


class ToolError(Exception):
    """Structured actionable error for MCP tools."""
    def __init__(self, error_code: str, message: str, retryable: bool = False):
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{error_code}] {message}")

    def to_dict(self) -> dict:
        return {
            "success": False,
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
        }


# --- Subtitle & Transcription ---
class SubtitleSegment(BaseModel):
    index: int
    start_time: str      # Format: "00:00:01,200"
    end_time: str        # Format: "00:00:09,500"
    start_seconds: float
    end_seconds: float
    text: str


class TranscribeInput(BaseModel):
    audio_path: str = Field(..., description="File path to the audio or video file to transcribe")
    language: Optional[str] = Field(None, description="ISO-639-1 code (e.g. 'hi', 'ta'). If None, auto-detected.")
    vad: bool = Field(True, description="Enable Voice Activity Detection")
    output_format: Literal["text", "srt", "segments"] = Field("text", description="Desired output format")


class TranscribeOutput(BaseModel):
    text: str
    language: Optional[str] = None
    srt_path: Optional[str] = None
    segments: Optional[List[SubtitleSegment]] = None
    duration_seconds: float
    cost_inr: float
    cached: bool
    recovery_attempts: Optional[List[str]] = None


# --- Translation ---
class TranslateInput(BaseModel):
    text: str = Field(..., description="The source text to translate")
    target_language_code: str = Field(..., description="Target language code (e.g. 'en', 'hi', 'kn', 'mr', 'ta')")
    source_language_code: Optional[str] = Field(None, description="Source language code hint if known")
    target_script: str = Field("native", description="Target script representation")
    preserve_formatting: bool = Field(True, description="Whether to preserve markdown tables and headings")


class TranslateOutput(BaseModel):
    translated_text: str
    source_language: Optional[str] = None
    target_language: str
    estimated_tokens: int
    cost_inr: float
    cached: bool
    text_artifact_id: Optional[str] = None


# --- Speech Synthesis ---
class SpeakInput(BaseModel):
    text: Optional[str] = Field(None, description="Text to synthesize into speech. Can be omitted if text_artifact_id is provided.")
    text_artifact_id: Optional[str] = Field(None, description="Reference path or artifact ID of translated text, avoiding rewriting Indic text.")
    language: str = Field(..., description="Language code (e.g. 'hi', 'en', 'ta')")
    voice: str = Field("Amit", description="Name of the voice to use")
    output_path: Optional[str] = Field(None, description="Path where the output audio file will be saved")


class SpeakOutput(BaseModel):
    audio_path: str
    duration_seconds: float
    character_count: int
    cost_inr: float
    cached: bool


# --- OCR ---
class OCRInput(BaseModel):
    file_path: str = Field(..., description="Path to image or scanned PDF document")
    page_numbers: Optional[List[int]] = Field(None, description="Specific pages to process (1-indexed)")


class OCROutput(BaseModel):
    text: str
    page_count: int
    tables_detected: int = 0
    cost_inr: float
    cached: bool


# --- Media Inspection ---
class InspectMediaOutput(BaseModel):
    file_path: str
    file_type: Literal["audio", "video", "image", "pdf", "text", "unknown"]
    size_bytes: int
    duration_seconds: Optional[float] = None
    channels: Optional[int] = None
    sample_rate: Optional[int] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    page_count: Optional[int] = None


# --- Language Detection ---
class DetectLangOutput(BaseModel):
    language: str
    script: str
    confidence: float
    is_assumed: bool

