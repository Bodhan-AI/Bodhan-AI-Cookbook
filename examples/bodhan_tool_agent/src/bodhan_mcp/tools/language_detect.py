"""
Language detection from text using Unicode script analysis.

For audio, language detection is done via Indic-Transcribe (see transcribe.py).
This module handles text-only detection based on character script ranges,
which is cheap, offline, and fast enough for pre-translation checks.
"""
from __future__ import annotations

import unicodedata

from .constants import SCRIPT_TO_LANG


def detect_language_from_text(text: str) -> dict:
    """Detect the dominant language in a text string.

    Counts characters per Unicode script. Returns the dominant script's
    associated BCP-47 language code, or 'en' for Latin-script text.

    Returns:
        {"language": str, "script": str, "confidence": float}
    """
    counts: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        try:
            char_name = unicodedata.name(ch, "")
        except ValueError:
            continue
        matched = False
        for script in SCRIPT_TO_LANG:
            if script.upper() in char_name.upper():
                counts[script] = counts.get(script, 0) + 1
                matched = True
                break
        if not matched and ch.isascii():
            counts["Latin"] = counts.get("Latin", 0) + 1

    if not counts:
        return {"language": "unknown", "script": "unknown", "confidence": 0.0}

    dominant = max(counts, key=lambda k: counts[k])
    total = sum(counts.values())
    confidence = counts[dominant] / total

    lang = "en" if dominant == "Latin" else SCRIPT_TO_LANG.get(dominant, "unknown")
    return {"language": lang, "script": dominant, "confidence": round(confidence, 3)}
