"""Pydantic input/output schemas for all MCP tools."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


# ── Language enums ────────────────────────────────────────────────────────────

class STTLanguage(str, Enum):
    hi = "hi"; bn = "bn"; ta = "ta"; te = "te"; mr = "mr"
    gu = "gu"; kn = "kn"; ml = "ml"; pa = "pa"; or_ = "or"
    as_ = "as"; ur = "ur"; sa = "sa"; mai = "mai"; bho = "bho"
    kok = "kok"; ne = "ne"; si = "si"


class TTSLanguage(str, Enum):
    hi = "hi"; bn = "bn"; ta = "ta"; te = "te"; mr = "mr"
    gu = "gu"; kn = "kn"; ml = "ml"; pa = "pa"


class TranslateTargetLanguage(str, Enum):
    hi = "hi"; bn = "bn"; ta = "ta"; te = "te"; mr = "mr"
    gu = "gu"; kn = "kn"; ml = "ml"; pa = "pa"; or_ = "or"
    as_ = "as"; ur = "ur"; sa = "sa"; mai = "mai"; bho = "bho"
    kok = "kok"; ne = "ne"; si = "si"; en = "en"


class TTSVoice(str, Enum):
    # Hindi
    Amit = "Amit"; Priya = "Priya"
    # Bengali
    Ravi = "Ravi"; Ananya = "Ananya"
    # Tamil
    Surya = "Surya"; Devi = "Devi"
    # Telugu
    Arjun = "Arjun"; Kavya = "Kavya"
    # Marathi
    Vikram = "Vikram"; Riya = "Riya"
    # Gujarati
    Kabir = "Kabir"; Nisha = "Nisha"
    # Kannada
    Kiran = "Kiran"; Deepa = "Deepa"
    # Malayalam
    Dev = "Dev"; Meera = "Meera"
    # Punjabi
    Jeet = "Jeet"; Simran = "Simran"


# ── Error schema ──────────────────────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False
    hint: str = ""


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: ErrorDetail


# ── STT schemas ───────────────────────────────────────────────────────────────

class TranscribeAudioInput(BaseModel):
    file_path: str = Field(
        description="Absolute path to the audio file (WAV, MP3, M4A, FLAC, OGG). "
                    "Files longer than 30 seconds are split automatically."
    )
    language: Optional[STTLanguage] = Field(
        default=None,
        description="ISO-639-1 language code. If omitted, Indic-Transcribe will "
                    "auto-detect the language. Only Indic languages are supported.",
    )
    vad: bool = Field(
        default=True,
        description="Enable voice-activity detection before splitting into chunks. "
                    "Keep True for natural speech; set False only for clean studio audio.",
    )


class TranscriptSegment(BaseModel):
    start_seconds: float
    end_seconds: float
    text: str


class TranscribeAudioOutput(BaseModel):
    ok: Literal[True] = True
    file_path: str
    detected_language: Optional[str]
    full_text: str
    segments: list[TranscriptSegment]
    duration_seconds: float
    chunks_processed: int
    cost_inr: float
    latency_seconds: float
    cached: bool = False


# ── Translate schemas ─────────────────────────────────────────────────────────

class TranslateTextInput(BaseModel):
    text: Optional[str] = Field(
        default=None,
        description="Text to translate. Supply either 'text' or 'file_path', not both.",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Path to a text or Markdown file to translate. "
                    "Supply either 'file_path' or 'text', not both.",
    )
    target_language: TranslateTargetLanguage = Field(
        description="ISO-639-1 target language code. Use 'en' for English."
    )
    source_language: Optional[str] = Field(
        default=None,
        description="ISO-639-1 source language hint. Auto-detected if omitted.",
    )

    @field_validator("text", "file_path", mode="before")
    @classmethod
    def at_least_one(cls, v: Any, info: Any) -> Any:
        return v


class TranslateTextOutput(BaseModel):
    ok: Literal[True] = True
    translated_text: str
    output_file_path: Optional[str] = None
    source_language_detected: Optional[str]
    target_language: str
    chunks_processed: int
    output_tokens: int
    cost_inr: float
    latency_seconds: float
    cached: bool = False


# ── TTS schemas ───────────────────────────────────────────────────────────────

class SynthesizeSpeechInput(BaseModel):
    text: str = Field(
        description="Text to synthesize. Must be in the target language. "
                    "Long texts are split on sentence boundaries automatically."
    )
    language: TTSLanguage = Field(
        description="Language of the input text. Only hi, bn, ta, te, mr, gu, kn, ml, pa are supported."
    )
    voice: TTSVoice = Field(
        description="Voice name. Must match the language: "
                    "hi→Amit/Priya, bn→Ravi/Ananya, ta→Surya/Devi, te→Arjun/Kavya, "
                    "mr→Vikram/Riya, gu→Kabir/Nisha, kn→Kiran/Deepa, ml→Dev/Meera, pa→Jeet/Simran."
    )
    output_path: Optional[str] = Field(
        default=None,
        description="Where to save the output WAV file. If omitted, a path is generated automatically.",
    )


class SynthesizeSpeechOutput(BaseModel):
    ok: Literal[True] = True
    output_file_path: str
    duration_seconds: float
    input_chars: int
    chunks_synthesized: int
    cost_inr: float
    latency_seconds: float
    cached: bool = False
    warning: Optional[str] = None


# ── OCR schemas ───────────────────────────────────────────────────────────────

class OCRDocumentInput(BaseModel):
    file_path: str = Field(
        description="Absolute path to an image (PNG, JPG, JPEG, TIFF, BMP) or PDF. "
                    "PDFs are rendered page-by-page; each page is OCR'd separately."
    )
    output_dir: Optional[str] = Field(
        default=None,
        description="Directory to store per-page text files. Auto-generated if omitted.",
    )


class OCRPageResult(BaseModel):
    page: int
    text: str
    text_file_path: str
    cost_inr: float
    cached: bool = False


class OCRDocumentOutput(BaseModel):
    ok: Literal[True] = True
    file_path: str
    total_pages: int
    pages: list[OCRPageResult]
    full_text: str
    full_text_file_path: str
    total_cost_inr: float
    latency_seconds: float


# ── Media helper schemas ──────────────────────────────────────────────────────

class InspectMediaInput(BaseModel):
    file_path: str = Field(description="Path to any media or document file to inspect.")


class InspectMediaOutput(BaseModel):
    ok: Literal[True] = True
    file_path: str
    mime_type: str
    size_bytes: int
    duration_seconds: Optional[float] = None   # audio/video
    num_pages: Optional[int] = None            # PDF
    width: Optional[int] = None               # image
    height: Optional[int] = None              # image
    detected_script: Optional[str] = None


class SplitAudioInput(BaseModel):
    file_path: str = Field(description="Audio file to split.")
    chunk_seconds: int = Field(default=30, ge=1, le=60, description="Max chunk duration in seconds.")
    output_dir: Optional[str] = Field(default=None, description="Directory for chunks. Auto if omitted.")


class SplitAudioOutput(BaseModel):
    ok: Literal[True] = True
    chunks: list[str]   # list of output file paths
    num_chunks: int


class ConcatAudioInput(BaseModel):
    file_paths: list[str] = Field(description="Ordered list of audio file paths to concatenate.")
    output_path: Optional[str] = Field(default=None, description="Output file path. Auto if omitted.")


class ConcatAudioOutput(BaseModel):
    ok: Literal[True] = True
    output_file_path: str
    duration_seconds: float


class RenderPDFPagesInput(BaseModel):
    file_path: str = Field(description="PDF file to render.")
    dpi: int = Field(default=200, ge=72, le=600, description="Render DPI.")
    output_dir: Optional[str] = Field(default=None, description="Directory for images. Auto if omitted.")


class RenderPDFPagesOutput(BaseModel):
    ok: Literal[True] = True
    page_image_paths: list[str]
    num_pages: int


class BuildSRTInput(BaseModel):
    segments: list[TranscriptSegment] = Field(description="Transcript segments with timing.")
    output_path: Optional[str] = Field(default=None, description="Output .srt path. Auto if omitted.")
    target_language: Optional[str] = Field(default=None, description="Optional target language code to translate subtitles into.")
    source_language: Optional[str] = Field(default=None, description="Optional source language code.")


class BuildSRTOutput(BaseModel):
    ok: Literal[True] = True
    srt_file_path: str
    num_entries: int


class DetectScriptInput(BaseModel):
    text: str = Field(description="Sample text to identify script/language from.", max_length=500)


class DetectScriptOutput(BaseModel):
    ok: Literal[True] = True
    detected_language: Optional[str]
    script_name: Optional[str]
    confidence: str  # "high" | "medium" | "low"


class GetSupportedLanguagesOutput(BaseModel):
    ok: Literal[True] = True
    stt_languages: list[str]
    translate_languages: list[str]
    tts_languages: list[str]
    tts_voices: dict[str, list[str]]


# ── Canonical aliases for compatibility across specs ──────────────────────────
SrtEntry = TranscriptSegment
TranscribeInput = TranscribeAudioInput
TranscribeOutput = TranscribeAudioOutput
TranslateInput = TranslateTextInput
TranslateOutput = TranslateTextOutput
SpeakInput = SynthesizeSpeechInput
SpeakOutput = SynthesizeSpeechOutput
OcrInput = OCRDocumentInput
OcrOutput = OCRDocumentOutput
RenderPdfInput = RenderPDFPagesInput
RenderPdfOutput = RenderPDFPagesOutput
BuildSrtInput = BuildSRTInput
BuildSrtOutput = BuildSRTOutput
GetLanguagesOutput = GetSupportedLanguagesOutput

