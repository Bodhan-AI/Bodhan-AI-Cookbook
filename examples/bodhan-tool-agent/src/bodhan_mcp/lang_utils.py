"""Language detection and validation utilities for Bodhan Indic models."""

from __future__ import annotations

import unicodedata
from typing import Tuple

# ISO 639-1 / 639-2 codes mapped to official English names
# Based on Bodhan AI API specifications (22 official Indic languages + English)
SUPPORTED_INDIC_LANGUAGES: dict[str, str] = {
    "as": "Assamese",
    "bhb": "Bhili",
    "bho": "Bhojpuri",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "kok": "Konkani",
    "ks": "Kashmiri",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}

# Reverse mapping from lower-case name to code
_NAME_TO_CODE: dict[str, str] = {
    name.lower(): code for code, name in SUPPORTED_INDIC_LANGUAGES.items()
}


def is_language_supported(lang_query: str) -> Tuple[bool, str | None, str]:
    """Checks whether a language (code or English name) is supported by Bodhan models.

    Returns:
        (is_supported, iso_code_or_none, message)
    """
    clean = lang_query.strip().lower()
    if not clean:
        return False, None, "No language provided."

    # Direct match on code
    if clean in SUPPORTED_INDIC_LANGUAGES:
        return True, clean, f"Supported: {SUPPORTED_INDIC_LANGUAGES[clean]} ({clean})"

    # Match on language name
    if clean in _NAME_TO_CODE:
        code = _NAME_TO_CODE[clean]
        return True, code, f"Supported: {SUPPORTED_INDIC_LANGUAGES[code]} ({code})"

    # Unsupported language
    return (
        False,
        None,
        f"Language {lang_query!r} is not supported. Bodhan models only support 22 official Indian languages and English.",
    )


def detect_script_language(text: str) -> str | None:
    """Detects primary language/script from Unicode character distribution.

    Returns ISO code (e.g. 'hi', 'ta', 'te', 'bn', 'en') or None if undetermined.
    """
    if not text:
        return None

    counts: dict[str, int] = {
        "hi": 0,  # Devanagari (Hindi, Marathi, Sanskrit, Nepali)
        "bn": 0,  # Bengali / Assamese
        "pa": 0,  # Gurmukhi (Punjabi)
        "gu": 0,  # Gujarati
        "or": 0,  # Odia
        "ta": 0,  # Tamil
        "te": 0,  # Telugu
        "kn": 0,  # Kannada
        "ml": 0,  # Malayalam
        "ur": 0,  # Arabic / Perso-Arabic (Urdu, Kashmiri, Sindhi)
        "en": 0,  # Basic Latin
    }

    for char in text:
        cp = ord(char)
        if 0x0900 <= cp <= 0x097F:
            counts["hi"] += 1
        elif 0x0980 <= cp <= 0x09FF:
            counts["bn"] += 1
        elif 0x0A00 <= cp <= 0x0A7F:
            counts["pa"] += 1
        elif 0x0A80 <= cp <= 0x0AFF:
            counts["gu"] += 1
        elif 0x0B00 <= cp <= 0x0B7F:
            counts["or"] += 1
        elif 0x0B80 <= cp <= 0x0BFF:
            counts["ta"] += 1
        elif 0x0C00 <= cp <= 0x0C7F:
            counts["te"] += 1
        elif 0x0C80 <= cp <= 0x0CFF:
            counts["kn"] += 1
        elif 0x0D00 <= cp <= 0x0D7F:
            counts["ml"] += 1
        elif 0x0600 <= cp <= 0x06FF:
            counts["ur"] += 1
        elif (0x0041 <= cp <= 0x005A) or (0x0061 <= cp <= 0x007A):
            counts["en"] += 1

    total_indic_or_latin = sum(counts.values())
    if total_indic_or_latin == 0:
        return None

    # Get dominant script
    top_lang, top_count = max(counts.items(), key=lambda item: item[1])
    if top_count / float(total_indic_or_latin) >= 0.3:
        return top_lang

    return None
