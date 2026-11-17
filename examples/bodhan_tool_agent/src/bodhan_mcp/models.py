#!/usr/bin/env python3
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


BODHAN_BASE_URL = "https://api.bodhan.ai"

SUPPORTED_LANGUAGES: frozenset[str] = frozenset([
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai",
    "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
    "ks-Deva", "sd-Arab", "mni-Beng",
])

SUPPORTED_SPEECH_LANGUAGES = frozenset([
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni",
    "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
])

SPEECH_VOICES = {
    "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]},
    "or": {"female": ["Itishree"], "male": ["Akash"]},
    "pa": {"female": ["Kaur"], "male": ["Manpreet"]},
    "as": {"female": ["Prastuti"], "male": ["Ankur"]},
    "ne": {"female": ["Srijana"], "male": ["Sagar"]},
    "ur": {"female": ["Saba"], "male": ["Zaid"]},
}

COST_PER_MINUTE_STT = 0.10
COST_PER_10K_OUTPUT_TOKENS_TRANSLATE = 0.20
COST_PER_10K_CHARS_SPEAK = 6.00
COST_PER_IMAGE_OCR = 0.20


class BodhanError(Exception):
    def __init__(self, message: str, code: str = "unknown"):
        super().__init__(message)
        self.code = code


class MediaInspectResult(BaseModel):
    format: str = Field(description="File format/extension")
    duration_sec: float = Field(description="Duration in seconds")
    bitrate: int = Field(description="Bitrate in bps")
    sample_rate: Optional[int] = Field(default=None, description="Sample rate in Hz")
    channels: Optional[int] = Field(default=None, description="Number of audio channels")
    language: str = Field(description="Detected language code")
    min_cost_inr: float = Field(description="Estimated minimum cost in INR")


CAPABILITIES = {
    "transcribe": {
        "description": "Speech-to-text transcription for Indian languages",
        "endpoint": "/v1/audio/transcriptions",
        "method": "POST",
        "content_type": "multipart/form-data",
        "parameters": {
            "file": {
                "type": "file",
                "required": True,
                "description": "Audio file (wav, mp3, etc.)"
            },
            "model": {"type": "string", "default": "indic-transcribe"},
            "language": {
                "type": "string",
                "required": False,
                "description": "Language hint (ISO code)"
            },
        },
        "supported_languages": sorted(list(SUPPORTED_SPEECH_LANGUAGES)),
        "cost_per_minute_inr": COST_PER_MINUTE_STT,
    },
    "translate": {
        "description": "Machine translation between Indian languages",
        "endpoint": "/translate",
        "method": "POST",
        "content_type": "application/json",
        "parameters": {
            "text": {"type": "string", "required": True},
            "source_language": {"type": "string", "required": True},
            "target_language": {"type": "string", "required": True},
        },
        "supported_languages": sorted(list(SUPPORTED_LANGUAGES)),
        "cost_per_10k_tokens_inr": COST_PER_10K_OUTPUT_TOKENS_TRANSLATE,
    },
    "speak": {
        "description": "Text-to-speech synthesis for Indian languages",
        "endpoint": "/v1/audio/speech",
        "method": "POST",
        "content_type": "application/json",
        "parameters": {
            "input": {"type": "string", "required": True},
            "voice": {"type": "string", "required": True},
            "instructions": {"type": "string", "required": False},
            "response_format": {"type": "string", "default": "wav"},
        },
        "supported_languages": sorted(list(SUPPORTED_SPEECH_LANGUAGES)),
        "voices": SPEECH_VOICES,
        "cost_per_10k_chars_inr": COST_PER_10K_CHARS_SPEAK,
    },
    "ocr": {
        "description": "Optical character recognition for Indian language documents",
        "endpoint": "/v1/chat/completions",
        "method": "POST",
        "content_type": "application/json",
        "parameters": {
            "model": {"type": "string", "default": "indic-ocr"},
            "messages": {"type": "array", "required": True},
            "table_format": {"type": "string", "default": "markdown"},
        },
        "supported_image_formats": ["png", "jpg", "jpeg", "pdf"],
        "cost_per_image_inr": COST_PER_IMAGE_OCR,
    },
}


def check_language_supported(lang: str, is_speech: bool = False) -> bool:
    if is_speech:
        return lang in SUPPORTED_SPEECH_LANGUAGES
    return lang in SUPPORTED_LANGUAGES


def get_voice_for_language(lang: str, gender: str = "female") -> str:
    return SPEECH_VOICES.get(lang, {}).get(gender, ["Kavya"])[0]