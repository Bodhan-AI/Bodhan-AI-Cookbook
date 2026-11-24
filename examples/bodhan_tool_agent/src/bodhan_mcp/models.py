"""
Pydantic data models and schemas for Bodhan MCP tools.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# 13 Production-tier Indic languages + English
SUPPORTED_LANGUAGES = {
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "bn": "Bengali",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "or": "Odia",
    "pa": "Punjabi",
    "as": "Assamese",
    "ur": "Urdu",
    "sa": "Sanskrit",
    "en": "English",
}

# Supported audio extensions
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
DOC_EXTENSIONS = {".pdf"}


class ToolCallMetadata(BaseModel):
    cost_inr: float = Field(0.0, description="Cost of the operation in INR")
    latency_ms: float = Field(0.0, description="Execution latency in milliseconds")
    model_name: str = Field(..., description="Bodhan model used")


class TranscribeSegment(BaseModel):
    start_seconds: float
    end_seconds: float
    text: str


class TranscribeInput(BaseModel):
    audio_path: str = Field(..., description="Local path to audio file (WAV, MP3, etc.)")
    language: Optional[str] = Field(None, description="ISO-639 language code (e.g. 'hi', 'ta', 'te', 'en'). Detected if omitted.")
    model_variant: str = Field("core", description="'core' or 'flex'")


class TranscribeResult(BaseModel):
    text: str = Field(..., description="Complete transcribed text")
    language: str = Field(..., description="Detected or specified language")
    duration_seconds: float = Field(..., description="Total duration of processed audio in seconds")
    segments: List[TranscribeSegment] = Field(default_factory=list, description="Timestamped segments")
    metadata: ToolCallMetadata


class TranslateInput(BaseModel):
    text: str = Field(..., description="Text to translate")
    source_lang: Optional[str] = Field("en", description="Source ISO-639 language code (e.g. 'en', 'hi', 'ta')")
    target_lang: str = Field(..., description="Target ISO-639 language code (e.g. 'hi', 'ta', 'te', 'mr')")


class TranslateResult(BaseModel):
    translated_text: str = Field(..., description="Translated text with structure preserved")
    source_lang: str = Field(..., description="Source language code")
    target_lang: str = Field(..., description="Target language code")
    token_count: int = Field(..., description="Estimated output token count")
    metadata: ToolCallMetadata


class SpeakInput(BaseModel):
    text: str = Field(..., description="Text in target Indian language to synthesize")
    language: str = Field(..., description="ISO-639 language code of the text (e.g. 'hi', 'ta', 'bn')")
    output_audio_path: Optional[str] = Field(None, description="Path where generated audio should be saved")
    gender: Optional[str] = Field("female", description="Voice gender: 'female' or 'male'")


class SpeakResult(BaseModel):
    audio_path: str = Field(..., description="Path to the saved audio file")
    duration_seconds: float = Field(..., description="Duration of the generated audio in seconds")
    character_count: int = Field(..., description="Number of characters synthesized")
    metadata: ToolCallMetadata


class OCRInput(BaseModel):
    file_path: str = Field(..., description="Local path to an image (PNG, JPG) or scanned PDF")
    extract_tables: bool = Field(True, description="Whether to format tables as Markdown")
    extract_equations: bool = Field(True, description="Whether to preserve LaTeX equations")


class PageOCRResult(BaseModel):
    page_number: int
    text: str
    has_tables: bool = False
    has_equations: bool = False


class OCRResult(BaseModel):
    full_text: str = Field(..., description="Extracted document text across all pages in Markdown")
    pages: List[PageOCRResult] = Field(default_factory=list, description="Per-page OCR extraction results")
    page_count: int = Field(..., description="Total pages processed")
    metadata: ToolCallMetadata


class InspectMediaInput(BaseModel):
    file_path: str = Field(..., description="Path to media file (audio, image, PDF, video)")


class MediaInspectionResult(BaseModel):
    file_path: str
    file_type: str  # "audio", "image", "pdf", "video", "text", "unknown"
    file_size_bytes: int
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    page_count: Optional[int] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class DetectLanguageInput(BaseModel):
    text: Optional[str] = Field(None, description="Text snippet to inspect for script/language")
    audio_path: Optional[str] = Field(None, description="Audio file to detect language from")


class LanguageDetectionResult(BaseModel):
    detected_lang: str = Field(..., description="ISO-639 language code (e.g. 'hi', 'ta', 'te', 'en')")
    language_name: str = Field(..., description="Human-readable language name")
    confidence: float = Field(..., description="Confidence score 0.0 to 1.0")
    is_indic: bool = Field(..., description="Whether language is an Indian language")
    is_supported: bool = Field(..., description="Whether language is supported by Bodhan models")
    method: str = Field(..., description="Method used: 'script_analysis', 'transcribe_probe', etc.")
