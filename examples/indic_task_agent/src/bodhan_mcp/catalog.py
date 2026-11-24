"""What Bodhan supports: languages per model, voices, styles, prices.

The data lives in catalog.json (generated from the Cookbook's scripts/bodhan_api_rules.json,
which mirrors the API docs) so it can be updated without touching code.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from .errors import BodhanToolError


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return json.loads(resources.files("bodhan_mcp").joinpath("catalog.json").read_text(encoding="utf-8"))


def language_names() -> dict[str, str]:
    return catalog()["language_names"]


STT_LANGS = frozenset(catalog()["speech_to_text"]["languages"])
TRANSLATE_LANGS = frozenset(catalog()["translation"]["target_languages"])
TTS_LANGS = frozenset(catalog()["speech_synthesis"]["languages"])
TTS_STYLES = tuple(catalog()["speech_synthesis"]["styles"])
VOICES: dict[str, dict[str, list[str]]] = catalog()["speech_synthesis"]["voices"]
PRICING = catalog()["pricing_inr"]

# Voice used for English, which has no voice of its own in the library (any voice reads any language).
ENGLISH_VOICES = {"female": ["Kavya", "Suhani"], "male": ["Amit"]}

# Extra spellings people use for the same languages.
_ALIASES = {
    "english": "en",
    "hindi": "hi",
    "tamil": "ta",
    "telugu": "te",
    "bengali": "bn",
    "bangla": "bn",
    "marathi": "mr",
    "gujarati": "gu",
    "kannada": "kn",
    "malayalam": "ml",
    "punjabi": "pa",
    "panjabi": "pa",
    "odia": "or",
    "oriya": "or",
    "assamese": "as",
    "urdu": "ur",
    "nepali": "ne",
    "sanskrit": "sa",
    "konkani": "kok",
    "maithili": "mai",
    "dogri": "doi",
    "bodo": "brx",
    "santali": "sat",
    "santhali": "sat",
    "kashmiri": "ks",
    "sindhi": "sd",
    "manipuri": "mni",
    "meitei": "mni",
    "bhojpuri": "bho",
    "bhili": "bhb",
}


def all_voice_names() -> dict[str, tuple[str, str]]:
    """voice name (lower-case) -> (language it was designed for, gender)."""
    out: dict[str, tuple[str, str]] = {}
    for lang, by_gender in VOICES.items():
        for gender, names in by_gender.items():
            for name in names:
                out[name.lower()] = (lang, gender)
    return out


def normalize_language(value: str | None) -> str | None:
    """Tamil, ta, TA or a regional tag like ta-IN -> ta. Script variants (ks-Deva) are kept. Unknown -> lower-cased input."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    low = raw.lower()
    if low in {"auto", "detect", "unknown"}:
        return "auto"
    if low in _ALIASES:
        return _ALIASES[low]
    for code in language_names():
        if code.lower() == low:
            return code
    if low.endswith("-in"):  # BCP-47 regional tags like hi-IN
        return normalize_language(low[:-3])
    return low


def require_language(value: str | None, supported: frozenset[str], capability: str) -> str:
    code = normalize_language(value)
    if not code or code == "auto" or code not in supported:
        names = language_names()
        listing = ", ".join(f"{c} ({names.get(c, c)})" for c in sorted(supported))
        raise BodhanToolError(
            "unsupported_language",
            f"{value!r} is not supported for {capability}.",
            hint=f"Supported language codes for {capability}: {listing}. If the user asked for a language outside this list, tell them it is unsupported rather than producing output.",
            details={"requested": value, "supported": sorted(supported)},
        )
    return code


def pick_voice(language: str, voice: str | None = None, gender: str | None = None) -> str:
    """Resolve the voice to use. An explicit voice wins; otherwise the first voice of the requested gender for that language."""
    known = all_voice_names()
    if voice:
        if voice.lower() not in known:
            raise BodhanToolError(
                "unsupported_voice",
                f"Voice {voice!r} is not in Bodhan's voice library.",
                hint="Call list_capabilities to see voices, or omit `voice` and pass `gender` instead.",
                details={"voices": sorted(n.title() for n in known)},
            )
        # Return the canonical capitalisation.
        for lang_voices in VOICES.values():
            for names in lang_voices.values():
                for name in names:
                    if name.lower() == voice.lower():
                        return name
    g = (gender or "female").lower()
    if g not in {"female", "male"}:
        raise BodhanToolError("invalid_input", f"gender must be 'female' or 'male', got {gender!r}.")
    pool = ENGLISH_VOICES if language == "en" else VOICES.get(language) or ENGLISH_VOICES
    return pool[g][0]


def capabilities_summary() -> dict[str, Any]:
    c = catalog()
    names = language_names()
    return {
        "speech_to_text": {
            "languages": {k: names.get(k, k) for k in sorted(STT_LANGS)},
            "notes": "Auto-detects language if none is given; the tool chunks audio of any length.",
        },
        "translation": {
            "target_languages": {k: names.get(k, k) for k in sorted(TRANSLATE_LANGS)},
            "scripts": c["translation"]["scripts"],
            "notes": "Source language is detected by the model.",
        },
        "speech_synthesis": {
            "languages": {k: names.get(k, k) for k in sorted(TTS_LANGS)},
            "voices": VOICES,
            "english_voices": ENGLISH_VOICES,
            "styles": list(TTS_STYLES),
        },
        "ocr": {
            "inputs": "PDF (any length), PNG, JPEG, WEBP, TIFF, BMP, HEIC-less images",
            "table_formats": c["ocr"]["table_formats"],
        },
        "pricing_inr": PRICING,
        "not_supported": "Any language not listed above (for example French, Spanish, German, Chinese, Japanese, Arabic).",
    }
