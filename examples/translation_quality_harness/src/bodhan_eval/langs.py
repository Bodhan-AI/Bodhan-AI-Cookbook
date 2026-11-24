"""Language codes, scripts and voices, taken from https://console.bodhan.ai/api-docs/."""

from __future__ import annotations

# Unicode script name prefix (as returned by unicodedata.name) for each translate target.
SCRIPTS: dict[str, str] = {
    "as": "BENGALI", "bn": "BENGALI", "mni-Beng": "BENGALI",
    "hi": "DEVANAGARI", "mr": "DEVANAGARI", "ne": "DEVANAGARI", "mai": "DEVANAGARI", "kok": "DEVANAGARI",
    "sa": "DEVANAGARI", "doi": "DEVANAGARI", "brx": "DEVANAGARI", "sd": "DEVANAGARI", "ks-Deva": "DEVANAGARI",
    "ur": "ARABIC", "ks": "ARABIC", "sd-Arab": "ARABIC",
    "gu": "GUJARATI", "pa": "GURMUKHI", "or": "ORIYA", "ta": "TAMIL", "te": "TELUGU",
    "kn": "KANNADA", "ml": "MALAYALAM", "mni": "MEETEI MAYEK", "sat": "OL CHIKI", "en": "LATIN",
}

# Indic-Speak production tier (model card). Other languages work but are preview quality.
SPEECH_PRODUCTION = {"en", "hi", "bn", "mr", "te", "ta", "gu", "kn", "ml", "or", "pa", "as", "ur"}

# One default voice per speech language (female first in the documented voice library).
VOICES: dict[str, str] = {
    "as": "Prastuti", "bn": "Ishita", "brx": "Gwrbw", "doi": "Preeti", "gu": "Dhara", "hi": "Kavya",
    "kn": "Deepika", "kok": "Anjali", "ks": "Zoon", "mai": "Vaidehi", "ml": "Lakshmi", "mni": "Thoibi",
    "mr": "Anagha", "ne": "Srijana", "or": "Itishree", "pa": "Kaur", "sa": "Bharati", "sat": "Phulmani",
    "sd": "Moomal", "ta": "Anitha", "te": "Sravani", "ur": "Saba", "en": "Kavya",
}


def speech_lang(lang: str) -> str:
    """Map a translate code (e.g. 'ks-Deva') to the Indic-Speak / Indic-Transcribe code ('ks')."""
    return lang.split("-")[0]


def validate_langs(langs: list[str]) -> list[str]:
    unknown = [lang for lang in langs if lang not in SCRIPTS]
    if unknown:
        raise ValueError(f"unsupported language code(s) {unknown}; accepted: {sorted(SCRIPTS)}")
    return langs
