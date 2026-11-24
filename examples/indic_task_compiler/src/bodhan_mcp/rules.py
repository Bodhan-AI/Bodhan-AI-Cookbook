"""Vendored Bodhan API capability rules, language tables, voice library, and pricing."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent / "data" / "bodhan_api_rules.json"

# Hosted pricing in INR (from assignment brief).
PRICING_INR: dict[str, dict[str, Any]] = {
    "indic-transcribe": {
        "unit": "audio_minute",
        "rate_inr": 0.10,
        "notes": "Billed per second of input audio (₹0.10 / minute).",
    },
    "indic-translate": {
        "unit": "10k_output_tokens",
        "rate_inr": 0.20,
        "notes": "Input tokens free; billed per 10,000 output tokens (₹0.20 / 10k).",
    },
    "indic-speak": {
        "unit": "10k_input_chars",
        "rate_inr": 6.00,
        "notes": "Billed per 10,000 input characters (₹6.00 / 10k).",
    },
    "indic-ocr": {
        "unit": "image_page",
        "rate_inr": 0.20,
        "notes": "Billed per page image (₹0.20 / image).",
    },
}

# Common aliases and variants -> Bodhan ISO code.
LANGUAGE_ALIASES: dict[str, str] = {
    "assamese": "as",
    "bhili": "bhb",
    "bhojpuri": "bho",
    "bengali": "bn",
    "bangla": "bn",
    "bodo": "brx",
    "dogri": "doi",
    "english": "en",
    "gujarati": "gu",
    "hindi": "hi",
    "kannada": "kn",
    "konkani": "kok",
    "kashmiri": "ks",
    "kashmiri devanagari": "ks-Deva",
    "kashmiri perso-arabic": "ks",
    "maithili": "mai",
    "malayalam": "ml",
    "manipuri": "mni",
    "meitei": "mni",
    "manipuri bengali": "mni-Beng",
    "marathi": "mr",
    "nepali": "ne",
    "odia": "or",
    "oriya": "or",
    "punjabi": "pa",
    "panjabi": "pa",
    "sanskrit": "sa",
    "santali": "sat",
    "sindhi": "sd",
    "sindhi devanagari": "sd",
    "sindhi arabic": "sd-Arab",
    "sindhi perso-arabic": "sd-Arab",
    "tamil": "ta",
    "telugu": "te",
    "urdu": "ur",
}

# Well-known languages NOT supported by Bodhan, mapped to ISO codes for deterministic rejection.
UNSUPPORTED_LANGUAGES: dict[str, str] = {
    "french": "fr",
    "fr": "fr",
    "spanish": "es",
    "es": "es",
    "german": "de",
    "de": "de",
    "japanese": "ja",
    "ja": "ja",
    "chinese": "zh",
    "mandarin": "zh",
    "cantonese": "zh",
    "zh": "zh",
    "korean": "ko",
    "ko": "ko",
    "arabic": "ar",
    "ar": "ar",
    "russian": "ru",
    "ru": "ru",
    "italian": "it",
    "it": "it",
    "portuguese": "pt",
    "pt": "pt",
    "dutch": "nl",
    "nl": "nl",
    "turkish": "tr",
    "tr": "tr",
    "thai": "th",
    "th": "th",
    "vietnamese": "vi",
    "vi": "vi",
    "indonesian": "id",
    "id": "id",
    "malay": "ms",
    "ms": "ms",
    "persian": "fa",
    "farsi": "fa",
    "fa": "fa",
    "polish": "pl",
    "pl": "pl",
    "swedish": "sv",
    "sv": "sv",
    "greek": "el",
    "el": "el",
    "hebrew": "he",
    "he": "he",
    "swahili": "sw",
    "sw": "sw",
    "latin": "la",
    "klingon": "tlh",
    "esperanto": "eo",
}

# Unicode codepoint blocks -> candidate Bodhan ISO codes.
SCRIPT_RANGES: list[tuple[str, int, int, list[str]]] = [
    ("Devanagari", 0x0900, 0x097F, ["hi", "mr", "ne", "mai", "sa", "kok", "doi", "brx", "sd", "ks-Deva"]),
    ("Bengali", 0x0980, 0x09FF, ["bn", "as", "mni-Beng"]),
    ("Gurmukhi", 0x0A00, 0x0A7F, ["pa"]),
    ("Gujarati", 0x0A80, 0x0AFF, ["gu"]),
    ("Oriya", 0x0B00, 0x0B7F, ["or"]),
    ("Tamil", 0x0B80, 0x0BFF, ["ta"]),
    ("Telugu", 0x0C00, 0x0C7F, ["te"]),
    ("Kannada", 0x0C80, 0x0CFF, ["kn"]),
    ("Malayalam", 0x0D00, 0x0D7F, ["ml"]),
    ("Arabic", 0x0600, 0x06FF, ["ur", "ks", "sd-Arab"]),
    ("OlChiki", 0x1C50, 0x1C7F, ["sat"]),
    ("MeeteiMayek", 0xABC0, 0xABFF, ["mni"]),
    ("Latin", 0x0041, 0x024F, ["en"]),
]

# Expected primary Unicode script name per ISO language code (for script-ratio verification).
LANGUAGE_PRIMARY_SCRIPT: dict[str, str] = {
    "hi": "Devanagari",
    "mr": "Devanagari",
    "ne": "Devanagari",
    "mai": "Devanagari",
    "sa": "Devanagari",
    "kok": "Devanagari",
    "doi": "Devanagari",
    "brx": "Devanagari",
    "sd": "Devanagari",
    "ks-Deva": "Devanagari",
    "bhb": "Devanagari",
    "bho": "Devanagari",
    "bn": "Bengali",
    "as": "Bengali",
    "mni-Beng": "Bengali",
    "pa": "Gurmukhi",
    "gu": "Gujarati",
    "or": "Oriya",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "ur": "Arabic",
    "ks": "Arabic",
    "sd-Arab": "Arabic",
    "sat": "OlChiki",
    "mni": "MeeteiMayek",
    "en": "Latin",
}


@lru_cache(maxsize=1)
def load_api_rules() -> dict[str, Any]:
    """Load the vendored Bodhan API capability table."""
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def language_names() -> dict[str, str]:
    """Return ISO code -> human language name mapping."""
    return dict(load_api_rules()["language_names"])


def supported_languages(model: str) -> list[str]:
    """Return sorted list of supported ISO language codes for a Bodhan model."""
    models = load_api_rules()["models"]
    if model not in models:
        return []
    if "languages" in models[model]:
        return list(models[model]["languages"])
    # indic-ocr supports all Indic scripts + English.
    return sorted(load_api_rules()["language_names"].keys())


def is_language_supported(model: str, lang_code: str) -> bool:
    """Check whether a Bodhan model supports the given ISO language code."""
    langs = supported_languages(model)
    if lang_code in langs:
        return True
    # Also accept base code if variant was provided for models that only list base codes.
    base = lang_code.split("-")[0]
    return base in langs


def resolve_language(raw: str | None) -> tuple[str | None, bool]:
    """Deterministically resolve a language name or ISO code.

    Returns (iso_code, is_bodhan_known):
      - (iso_code, True) if known in Bodhan's language table
      - (iso_code, False) if known unsupported language (e.g. "French" -> ("fr", False))
      - (None, False) if empty or unrecognized
    """
    if not raw or not raw.strip():
        return None, False
    cleaned = raw.strip()
    names = language_names()
    # Direct exact code match (case-sensitive first, then case-insensitive).
    if cleaned in names:
        return cleaned, True
    lower = re.sub(r"\s+", " ", cleaned.lower())
    # Handle BCP-47 like "hi-in" or "ta_IN" by normalizing base if valid, while preserving script suffixes.
    code_by_lower = {k.lower(): k for k in names}
    if lower in code_by_lower:
        return code_by_lower[lower], True
    if lower in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[lower], True
    # Match against display names (e.g. "Tamil", "Marathi", "Kashmiri (Devanagari)").
    for code, display in names.items():
        if lower == display.lower() or lower == display.lower().split("(")[0].strip():
            return code, True
    # Handle country-tagged locale codes by taking the ISO language prefix.
    if re.fullmatch(r"[a-z]{2,3}[-_][a-z]{2}", lower):
        base = re.split(r"[-_]", lower)[0]
        if base in code_by_lower:
            return code_by_lower[base], True
        if base in UNSUPPORTED_LANGUAGES:
            return UNSUPPORTED_LANGUAGES[base], False
    if lower in UNSUPPORTED_LANGUAGES:
        return UNSUPPORTED_LANGUAGES[lower], False
    return None, False


def all_voices() -> dict[str, dict[str, list[str]]]:
    """Return voice catalog grouped by language and gender."""
    return dict(load_api_rules()["models"]["indic-speak"]["voices"])


def supported_styles() -> list[str]:
    """Return allowed Indic-Speak speaking styles."""
    return list(load_api_rules()["models"]["indic-speak"]["styles"])


def select_voice(
    language: str,
    voice: str | None = None,
    gender: str | None = None,
) -> tuple[str | None, str | None]:
    """Select and validate an Indic-Speak voice for the given language.

    Returns (voice_name, error_message).
    """
    voices_by_lang = all_voices()
    base_lang = language.split("-")[0]
    if not is_language_supported("indic-speak", base_lang):
        return None, (
            f"Language '{language}' is not supported by indic-speak. "
            f"Supported: {', '.join(supported_languages('indic-speak'))}"
        )

    # English uses Hindi/Indic voices per cookbook convention (Kavya/Amit).
    lang_entry = voices_by_lang.get(base_lang) or voices_by_lang["hi"]
    all_valid_names = {
        v
        for lang_voices in voices_by_lang.values()
        for g_list in lang_voices.values()
        for v in g_list
    }
    lang_valid_names = [v for g_list in lang_entry.values() for v in g_list]

    if voice:
        # Match case-insensitively to be forgiving for third-party MCP clients.
        lower_map = {v.lower(): v for v in lang_valid_names}
        if voice.lower() in lower_map:
            return lower_map[voice.lower()], None
        all_lower = {v.lower(): v for v in all_valid_names}
        if voice.lower() in all_lower and base_lang == "en":
            return all_lower[voice.lower()], None
        return None, (
            f"Voice '{voice}' is not valid for language '{base_lang}'. "
            f"Available voices for '{base_lang}': {', '.join(lang_valid_names)}"
        )

    if gender:
        g = gender.strip().lower()
        if g not in ("female", "male"):
            return None, f"Invalid gender '{gender}'. Expected 'female' or 'male'."
        choices = lang_entry.get(g, [])
        if choices:
            return choices[0], None

    # Default: first female voice, else first male voice.
    if lang_entry.get("female"):
        return lang_entry["female"][0], None
    return lang_entry["male"][0], None
