#!/usr/bin/env python3
"""Language-name normalization shared by translate/transcribe/speak tools."""
from __future__ import annotations

from typing import Optional

from ..models import (
    SUPPORTED_LANGUAGES,
    SUPPORTED_TRANSLATE_LANGUAGES,
    SUPPORTED_TTS_LANGUAGES,
    SUPPORTED_TRANSCRIBE_LANGUAGES,
    SUPPORTED_OCR_LANGUAGES,
    ErrorCode,
)


# ponytail: case-insensitive English name -> ISO-639 code mapping.
_NAME_TO_CODE: dict[str, str] = {
    "assamese": "as", "bhili": "bhb", "bhojpuri": "bho", "bengali": "bn",
    "bodo": "brx", "dogri": "doi", "english": "en", "gujarati": "gu",
    "hindi": "hi", "kannada": "kn", "konkani": "kok",
    "kashmiri": "ks", "kashmiri (devanagari)": "ks-Deva",
    "maithili": "mai", "malayalam": "ml",
    "manipuri": "mni", "manipuri (bengali script)": "mni-Beng",
    "marathi": "mr", "nepali": "ne", "odia": "or", "punjabi": "pa",
    "sanskrit": "sa", "santali": "sat", "sindhi": "sd",
    "sindhi (perso-arabic)": "sd-Arab", "tamil": "ta", "telugu": "te",
    "urdu": "ur",
}


def normalize_lang(value: Optional[str]) -> Optional[str]:
    """Map user-supplied language token to ISO-639 code."""
    if not value:
        return value
    v = value.strip()
    if v in SUPPORTED_LANGUAGES:
        return v
    mapped = _NAME_TO_CODE.get(v.lower())
    return mapped if mapped else v


def _error(code: ErrorCode, message: str, retryable: bool = False,
           details: dict | None = None) -> dict:
    return {
        "error": code.value,
        "message": message,
        "code": code.value,
        "retryable": retryable,
        "details": details or {},
    }


def validate_lang(
    value: Optional[str], capability: str,
) -> tuple[Optional[str], Optional[dict]]:
    """Normalize then validate a language token."""
    if not value:
        return None, _error(
            ErrorCode.VALIDATION_ERROR, "Language must be provided",
            retryable=False,
        )
    code = normalize_lang(value)
    sets = {
        "transcribe": SUPPORTED_TRANSCRIBE_LANGUAGES,
        "translate": SUPPORTED_TRANSLATE_LANGUAGES,
        "tts": SUPPORTED_TTS_LANGUAGES,
        "ocr": SUPPORTED_OCR_LANGUAGES,
    }
    allowed = sets.get(capability, SUPPORTED_LANGUAGES)
    if code not in allowed:
        return None, _error(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            f"Language '{value}' not supported for {capability}",
            retryable=False,
            details={"supported": sorted(allowed)},
        )
    return code, None


__all__ = ["normalize_lang", "validate_lang"]
