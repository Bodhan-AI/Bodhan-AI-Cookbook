"""
Language detection by Unicode script and character-set heuristics.

No API calls — pure code. Used to detect the language of text when unstated.
This is a helper tool exposed via MCP.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

# ---------------------------------------------------------------------------
# Unicode block → language mapping
# ---------------------------------------------------------------------------
SCRIPT_LANGUAGE_MAP: dict[str, str] = {
    "DEVANAGARI": "hi",       # Hindi (also Marathi, Sanskrit, Nepali, etc.)
    "BENGALI": "bn",          # Bengali / Assamese
    "TAMIL": "ta",
    "TELUGU": "te",
    "KANNADA": "kn",
    "MALAYALAM": "ml",
    "GUJARATI": "gu",
    "GURMUKHI": "pa",         # Punjabi
    "ORIYA": "or",            # Odia
    "OL CHIKI": "sat",        # Santali
    "MEETEI MAYEK": "mni",    # Manipuri (Meitei)
    "ARABIC": "ur",           # Urdu / Sindhi / Kashmiri (Perso-Arabic)
    "LATIN": "en",            # English (default for Latin)
}

# More specific Devanagari disambiguation (by common words)
DEVANAGARI_LANGUAGE_HINTS: dict[str, list[str]] = {
    "hi": ["है", "का", "की", "में", "और", "को", "से", "पर", "ने", "यह", "वह", "हैं", "कि", "नहीं"],
    "mr": ["आहे", "आणि", "हे", "या", "ती", "तो", "करण", "म्हण", "काय", "नाही", "आहेत"],
    "sa": ["अस्ति", "भवति", "तस्य", "एव", "च", "तु", "यत्र", "तत्र"],
    "ne": ["छ", "हो", "गर", "भने", "गरेको", "भएको"],
}

# Language code → full name
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "ta": "Tamil",
    "te": "Telugu", "mr": "Marathi", "gu": "Gujarati", "kn": "Kannada",
    "ml": "Malayalam", "pa": "Punjabi", "or": "Odia", "as": "Assamese",
    "ur": "Urdu", "ne": "Nepali", "sa": "Sanskrit", "sd": "Sindhi",
    "kok": "Konkani", "doi": "Dogri", "mai": "Maithili", "brx": "Bodo",
    "sat": "Santali", "ks": "Kashmiri", "mni": "Manipuri",
}


def detect_language(text: str) -> dict[str, Any]:
    """
    Detect the language of text using Unicode script analysis.

    This is a heuristic — it identifies the dominant script and infers
    the most likely language. For Devanagari text, it also checks common
    word patterns to distinguish Hindi from Marathi, Sanskrit, etc.

    Args:
        text: The text to analyze.

    Returns:
        dict with 'language_code', 'language_name', 'script', 'confidence',
        'script_distribution'.
    """
    if not text or not text.strip():
        return {
            "language_code": "unknown",
            "language_name": "Unknown",
            "script": "unknown",
            "confidence": 0.0,
            "script_distribution": {},
        }

    # Count characters by Unicode script
    script_counts: Counter[str] = Counter()
    total_letters = 0

    for char in text:
        if char.isspace() or char in ".,;:!?\"'()-[]{}0123456789":
            continue
        try:
            name = unicodedata.name(char, "")
            # Extract script from Unicode name (e.g., "DEVANAGARI LETTER KA" → "DEVANAGARI")
            parts = name.split()
            if len(parts) >= 2:
                script = parts[0]
                # Handle two-word scripts
                if script in {"OL", "MEETEI"} and len(parts) >= 3:
                    script = f"{parts[0]} {parts[1]}"
                script_counts[script] += 1
                total_letters += 1
            elif char.isalpha():
                script_counts["LATIN"] += 1
                total_letters += 1
        except ValueError:
            continue

    if total_letters == 0:
        return {
            "language_code": "unknown",
            "language_name": "Unknown",
            "script": "unknown",
            "confidence": 0.0,
            "script_distribution": {},
        }

    # Find dominant script
    dominant_script, dominant_count = script_counts.most_common(1)[0]
    confidence = dominant_count / total_letters

    # Build distribution
    distribution = {
        script: round(count / total_letters, 3)
        for script, count in script_counts.most_common(5)
    }

    # Map script to language
    lang_code = SCRIPT_LANGUAGE_MAP.get(dominant_script, "unknown")

    # For Devanagari, try to disambiguate
    if dominant_script == "DEVANAGARI":
        lang_code = _disambiguate_devanagari(text)

    language_name = LANGUAGE_NAMES.get(lang_code, "Unknown")

    return {
        "language_code": lang_code,
        "language_name": language_name,
        "script": dominant_script,
        "confidence": round(confidence, 3),
        "script_distribution": distribution,
    }


def _disambiguate_devanagari(text: str) -> str:
    """Try to distinguish Hindi, Marathi, Sanskrit, Nepali from Devanagari text."""
    text_lower = text.strip()
    scores: dict[str, int] = {}

    for lang, markers in DEVANAGARI_LANGUAGE_HINTS.items():
        score = sum(1 for m in markers if m in text_lower)
        if score > 0:
            scores[lang] = score

    if scores:
        return max(scores, key=scores.get)  # type: ignore

    # Default to Hindi (most common Devanagari language)
    return "hi"


def detect_language_code(text: str) -> str:
    """Shortcut: returns just the language code string."""
    return detect_language(text)["language_code"]


def get_language_name(code: str) -> str:
    """Get the full language name from a code."""
    return LANGUAGE_NAMES.get(code, f"Unknown ({code})")


def is_supported_language(code: str) -> bool:
    """Check if a language code is supported by Bodhan Translate."""
    from bodhan_mcp.client import SUPPORTED_LANGUAGE_CODES
    return code in SUPPORTED_LANGUAGE_CODES
