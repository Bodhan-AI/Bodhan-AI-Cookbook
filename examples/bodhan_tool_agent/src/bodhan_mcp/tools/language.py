"""Zero-model Unicode script and language identification for Bodhan MCP Server."""

from __future__ import annotations

import unicodedata
from typing import Dict, List, Optional, Tuple

# Unicode code point ranges for Indic and supported scripts
SCRIPT_RANGES: Dict[str, List[Tuple[int, int]]] = {
    "devanagari": [(0x0900, 0x097F)],
    "bengali": [(0x0980, 0x09FF)],
    "gurmukhi": [(0x0A00, 0x0A7F)],
    "gujarati": [(0x0A80, 0x0AFF)],
    "oriya": [(0x0B00, 0x0B7F)],
    "tamil": [(0x0B80, 0x0BFF)],
    "telugu": [(0x0C00, 0x0C7F)],
    "kannada": [(0x0C80, 0x0CFF)],
    "malayalam": [(0x0D00, 0x0D7F)],
    "ol_chiki": [(0x1C50, 0x1C7F)],
    "meetei_mayek": [(0xABC0, 0xABFF)],
    "perso_arabic": [
        (0x0600, 0x06FF),
        (0x0750, 0x077F),
        (0x08A0, 0x08FF),
        (0xFB50, 0xFDFF),
        (0xFE70, 0xFEFF),
    ],
    "latin": [(0x0041, 0x005A), (0x0061, 0x007A)],
}

# Mapping from canonical 2-3 letter ISO language codes to script family
LANGUAGE_TO_SCRIPT: Dict[str, str] = {
    "hi": "devanagari",
    "mr": "devanagari",
    "ne": "devanagari",
    "sa": "devanagari",
    "mai": "devanagari",
    "brx": "devanagari",
    "doi": "devanagari",
    "kok": "devanagari",
    "bn": "bengali",
    "as": "bengali",
    "pa": "gurmukhi",
    "gu": "gujarati",
    "or": "oriya",
    "ta": "tamil",
    "te": "telugu",
    "kn": "kannada",
    "ml": "malayalam",
    "ur": "perso_arabic",
    "ks": "perso_arabic",
    "sd": "perso_arabic",
    "sat": "ol_chiki",
    "mni": "meetei_mayek",
    "en": "latin",
}


def detect_script_language(text: str) -> Optional[str]:
    """Detects likely language/script cluster from Unicode character distribution without external models.

    Devanagari is shared by hi, mr, ne, sa, mai, brx, doi, kok.
    Perso-Arabic is shared by ur, ks, sd.
    In ambiguous cases, returns the shared cluster descriptor (e.g. 'hi/mr/ne' or 'ur/ks/sd').
    """
    if not text:
        return None

    counts: Dict[str, int] = {script: 0 for script in SCRIPT_RANGES}
    for ch in text:
        cp = ord(ch)
        for script, ranges in SCRIPT_RANGES.items():
            if any(start <= cp <= end for start, end in ranges):
                counts[script] += 1
                break

    best_script = max(counts, key=counts.get)
    if counts[best_script] == 0:
        return None

    if best_script == "devanagari":
        return "hi/mr/ne"
    elif best_script == "perso_arabic":
        return "ur/ks/sd"
    elif best_script == "bengali":
        # Check for Assamese-specific characters ৰ (U+09F0) or ৱ (U+09F1)
        if any(ord(c) in (0x09F0, 0x09F1) for c in text):
            return "as"
        return "bn"
    elif best_script == "gurmukhi":
        return "pa"
    elif best_script == "gujarati":
        return "gu"
    elif best_script == "oriya":
        return "or"
    elif best_script == "tamil":
        return "ta"
    elif best_script == "telugu":
        return "te"
    elif best_script == "kannada":
        return "kn"
    elif best_script == "malayalam":
        return "ml"
    elif best_script == "ol_chiki":
        return "sat"
    elif best_script == "meetei_mayek":
        return "mni"
    elif best_script == "latin":
        return "en"

    return None


def get_script_ratio(text: str, expected_lang: str) -> float:
    """Calculates ratio of characters in text matching the expected script for the language."""
    if not text:
        return 0.0
    script = LANGUAGE_TO_SCRIPT.get(expected_lang.lower())
    if not script or script not in SCRIPT_RANGES:
        return 1.0  # Cannot verify unknown script, treat as non-violating

    ranges = SCRIPT_RANGES[script]
    meaningful_chars = [
        ch for ch in text if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    ]
    if not meaningful_chars:
        return 0.0

    matching = sum(1 for ch in meaningful_chars if any(s <= ord(ch) <= e for s, e in ranges))
    return matching / len(meaningful_chars)
