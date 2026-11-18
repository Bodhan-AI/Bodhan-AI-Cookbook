#!/usr/bin/env python3
from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator


BODHAN_BASE_URL = "https://api.bodhan.ai/v1"

SUPPORTED_LANGUAGES: frozenset[str] = frozenset([
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
    "ks-Deva", "sd-Arab", "mni-Beng",
])

SUPPORTED_TRANSCRIBE_LANGUAGES: frozenset[str] = frozenset([
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "mai", "ml", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
])

SUPPORTED_TRANSLATE_LANGUAGES: frozenset[str] = frozenset([
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
    "ks-Deva", "sd-Arab", "mni-Beng",
])

SUPPORTED_TTS_LANGUAGES: frozenset[str] = frozenset([
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni",
    "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
])

SUPPORTED_OCR_LANGUAGES: frozenset[str] = frozenset([
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni",
    "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
])

SUPPORTED_TRANSLITERATE_LANGUAGES: frozenset[str] = frozenset([
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
    "ks-Deva", "sd-Arab", "mni-Beng",
])

TTS_VOICES: dict[str, dict[str, list[str]]] = {
    "as": {"female": ["Prastuti"], "male": ["Ankur"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "brx": {"female": ["Gwrbw"], "male": ["Sansuma"]},
    "doi": {"female": ["Preeti"], "male": ["Sham"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]},
    "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]},
    "kok": {"female": ["Anjali"], "male": ["Sandeep"]},
    "ks": {"female": ["Zoon"], "male": ["Ishfaq"]},
    "mai": {"female": ["Vaidehi"], "male": ["Madhukar"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]},
    "mni": {"female": ["Thoibi"], "male": ["Chaoba"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]},
    "ne": {"female": ["Srijana"], "male": ["Sagar"]},
    "or": {"female": ["Itishree"], "male": ["Akash"]},
    "pa": {"female": ["Kaur"], "male": ["Manpreet"]},
    "sa": {"female": ["Bharati"], "male": ["Aryaman"]},
    "sat": {"female": ["Phulmani"], "male": ["Sibu"]},
    "sd": {"female": ["Moomal"], "male": ["Rano"]},
    "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]},
    "ur": {"female": ["Saba"], "male": ["Zaid"]},
}

TTS_STYLES: frozenset[str] = frozenset([
    "AIR style news", "TV style news", "news", "Customer Care",
    "advertisements", "educational lecture", "children's stories",
    "single person narration audiobook", "happy", "sad", "anger",
    "fear", "disgust", "surprise",
])

TRANSLATE_SCRIPTS: frozenset[str] = frozenset(["native", "roman", "codemix"])
TRANSLITERATE_SCRIPTS: frozenset[str] = frozenset(["roman", "native"])
OCR_TABLE_FORMATS: frozenset[str] = frozenset(["html", "markdown"])

COST_PER_MINUTE_STT = 0.10
COST_PER_10K_OUTPUT_TOKENS_TRANSLATE = 0.20
COST_PER_10K_CHARS_SPEAK = 6.00
COST_PER_IMAGE_OCR = 0.20


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    UNSUPPORTED_VOICE = "UNSUPPORTED_VOICE"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    MEDIA_PROCESSING_ERROR = "MEDIA_PROCESSING_ERROR"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    UNKNOWN = "UNKNOWN"


class BodhanError(Exception):
    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.UNKNOWN,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}


class StructuredError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class LanguageInfo(BaseModel):
    code: str
    name: str
    transcribe: bool = False
    translate: bool = False
    transliterate: bool = False
    tts: bool = False
    ocr: bool = False


LANGUAGE_REGISTRY: dict[str, LanguageInfo] = {
    "as": LanguageInfo(
        code="as", name="Assamese", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "bhb": LanguageInfo(
        code="bhb", name="Bhili", transcribe=True, translate=False,
        transliterate=False, tts=False, ocr=False
    ),
    "bho": LanguageInfo(
        code="bho", name="Bhojpuri", transcribe=True, translate=False,
        transliterate=False, tts=False, ocr=False
    ),
    "bn": LanguageInfo(
        code="bn", name="Bengali", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "brx": LanguageInfo(
        code="brx", name="Bodo", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "doi": LanguageInfo(
        code="doi", name="Dogri", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "en": LanguageInfo(
        code="en", name="English", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "gu": LanguageInfo(
        code="gu", name="Gujarati", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "hi": LanguageInfo(
        code="hi", name="Hindi", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "kn": LanguageInfo(
        code="kn", name="Kannada", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "kok": LanguageInfo(
        code="kok", name="Konkani", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "ks": LanguageInfo(
        code="ks", name="Kashmiri (Perso-Arabic)", transcribe=True,
        translate=True, transliterate=True, tts=True, ocr=True
    ),
    "ks-Deva": LanguageInfo(
        code="ks-Deva", name="Kashmiri (Devanagari)", transcribe=False,
        translate=True, transliterate=True, tts=False, ocr=False
    ),
    "mai": LanguageInfo(
        code="mai", name="Maithili", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "ml": LanguageInfo(
        code="ml", name="Malayalam", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "mni": LanguageInfo(
        code="mni", name="Manipuri (Meitei Mayek)", transcribe=False,
        translate=True, transliterate=True, tts=True, ocr=True
    ),
    "mni-Beng": LanguageInfo(
        code="mni-Beng", name="Manipuri (Bengali script)",
        transcribe=False, translate=True, transliterate=True, tts=False, ocr=False
    ),
    "mr": LanguageInfo(
        code="mr", name="Marathi", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "ne": LanguageInfo(
        code="ne", name="Nepali", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "or": LanguageInfo(
        code="or", name="Odia", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "pa": LanguageInfo(
        code="pa", name="Punjabi", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "sa": LanguageInfo(
        code="sa", name="Sanskrit", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "sat": LanguageInfo(
        code="sat", name="Santali", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "sd": LanguageInfo(
        code="sd", name="Sindhi (Devanagari)", transcribe=True,
        translate=True, transliterate=True, tts=True, ocr=True
    ),
    "sd-Arab": LanguageInfo(
        code="sd-Arab", name="Sindhi (Perso-Arabic)", transcribe=False,
        translate=True, transliterate=True, tts=False, ocr=False
    ),
    "ta": LanguageInfo(
        code="ta", name="Tamil", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "te": LanguageInfo(
        code="te", name="Telugu", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
    "ur": LanguageInfo(
        code="ur", name="Urdu", transcribe=True, translate=True,
        transliterate=True, tts=True, ocr=True
    ),
}


class TransliterateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    language: str
    script: str = Field(default="roman")
    max_output_tokens: int | None = Field(default=None, ge=1, le=8192)

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language: {v}")
        return v

    @field_validator("script")
    @classmethod
    def validate_script(cls, v: str) -> str:
        if v not in TRANSLITERATE_SCRIPTS:
            opts = ", ".join(sorted(TRANSLITERATE_SCRIPTS))
            raise ValueError(f"Unsupported script: {v}. Must be one of {opts}")
        return v


class TransliterateResponse(BaseModel):
    transliteration: str
    language: str
    script: str
    truncated: bool


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)
    target_language: str
    script: str = Field(default="native")
    max_output_tokens: int | None = Field(default=None, ge=1, le=8192)

    @field_validator("target_language")
    @classmethod
    def validate_target_language(cls, v: str) -> str:
        if v not in SUPPORTED_TRANSLATE_LANGUAGES:
            raise ValueError(f"Unsupported target language: {v}")
        return v

    @field_validator("script")
    @classmethod
    def validate_script(cls, v: str) -> str:
        if v not in TRANSLATE_SCRIPTS:
            opts = ", ".join(sorted(TRANSLATE_SCRIPTS))
            raise ValueError(f"Unsupported script: {v}. Must be one of {opts}")
        return v


class TranslateUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class TranslateResponse(BaseModel):
    translation: str
    target_language: str
    script: str
    truncated: bool
    usage: TranslateUsage = Field(default_factory=TranslateUsage)


class TranscribeRequest(BaseModel):
    language: str | None = None
    max_duration_sec: int | None = Field(default=None, ge=1, le=3600)

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str | None) -> str | None:
        if v and v not in SUPPORTED_TRANSCRIBE_LANGUAGES:
            raise ValueError(f"Unsupported language for transcription: {v}")
        return v


class TranscribeSegment(BaseModel):
    text: str
    start_sec: float
    end_sec: float
    language: str | None = None


class TranscribeResponse(BaseModel):
    text: str
    segments: list[TranscribeSegment] = Field(default_factory=list)
    language: str | None = None
    duration_sec: float = 0.0


class SpeakRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=5000)
    voice: str
    instructions: str | None = None

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, v: str) -> str:
        all_voices = set()
        for lang_voices in TTS_VOICES.values():
            for gender_voices in lang_voices.values():
                all_voices.update(gender_voices)
        if v not in all_voices:
            raise ValueError(f"Unsupported voice: {v}")
        return v


class SpeakResponse(BaseModel):
    audio_data: bytes
    format: str = "wav"
    sample_rate: int = 24000
    channels: int = 1


class OCRBlock(BaseModel):
    order: int
    label: str
    type: str
    bbox_xyxy: list[int] = Field(..., min_length=4, max_length=4)
    conf: float
    text: str


class OCRRequest(BaseModel):
    image_data: str
    table_format: str = Field(default="markdown")
    max_tokens: int | None = Field(default=None, ge=1, le=8192)

    @field_validator("table_format")
    @classmethod
    def validate_table_format(cls, v: str) -> str:
        if v not in OCR_TABLE_FORMATS:
            opts = ", ".join(sorted(OCR_TABLE_FORMATS))
            raise ValueError(f"Unsupported table format: {v}. Must be one of {opts}")
        return v


class OCRResponse(BaseModel):
    content: str
    blocks: list[OCRBlock] = Field(default_factory=list)


class MediaInspectRequest(BaseModel):
    file_path: str


class MediaInspectResponse(BaseModel):
    format: str
    duration_sec: float
    bitrate: int
    sample_rate: int | None = None
    channels: int | None = None
    pages: int | None = None
    dimensions: tuple[int, int] | None = None
    language: str | None = None
    min_cost_inr: float


class CapabilitiesResponse(BaseModel):
    languages: list[LanguageInfo]
    voices: dict[str, dict[str, list[str]]]
    tts_styles: list[str]
    translate_scripts: list[str]
    transliterate_scripts: list[str]
    ocr_table_formats: list[str]
    pricing: dict[str, float]


def check_language_supported(lang: str, capability: str) -> bool:
    if capability == "transcribe":
        return lang in SUPPORTED_TRANSCRIBE_LANGUAGES
    elif capability == "translate":
        return lang in SUPPORTED_TRANSLATE_LANGUAGES
    elif capability == "tts":
        return lang in SUPPORTED_TTS_LANGUAGES
    elif capability == "ocr":
        return lang in SUPPORTED_OCR_LANGUAGES
    return lang in SUPPORTED_LANGUAGES


def get_voice_for_language(lang: str, gender: str = "female") -> str:
    return TTS_VOICES.get(lang, {}).get(gender, ["Kavya"])[0]


def estimate_transcribe_cost(duration_sec: float) -> float:
    return (duration_sec / 60.0) * COST_PER_MINUTE_STT


def estimate_translate_cost(output_tokens: int) -> float:
    return (output_tokens / 10000.0) * COST_PER_10K_OUTPUT_TOKENS_TRANSLATE


def estimate_speak_cost(input_chars: int) -> float:
    return (input_chars / 10000.0) * COST_PER_10K_CHARS_SPEAK


def estimate_ocr_cost(page_count: int = 1) -> float:
    return page_count * COST_PER_IMAGE_OCR

