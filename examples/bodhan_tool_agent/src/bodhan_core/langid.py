"""Script / character-set language identification (allowed by the rules; no ML, no external service)."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

_LIB = json.loads((Path(__file__).parent / "library.json").read_text(encoding="utf-8"))
LANGUAGE_NAMES: dict[str, str] = _LIB["language_names"]

# (name, start, end, default language, other languages written in the script)
_SCRIPTS = [
    ("Devanagari", 0x0900, 0x097F, "hi", ["mr", "ne", "mai", "kok", "doi", "sa", "brx", "bho", "sd", "ks"]),
    ("Bengali", 0x0980, 0x09FF, "bn", ["as", "mni"]),
    ("Gurmukhi", 0x0A00, 0x0A7F, "pa", []),
    ("Gujarati", 0x0A80, 0x0AFF, "gu", []),
    ("Odia", 0x0B00, 0x0B7F, "or", []),
    ("Tamil", 0x0B80, 0x0BFF, "ta", []),
    ("Telugu", 0x0C00, 0x0C7F, "te", []),
    ("Kannada", 0x0C80, 0x0CFF, "kn", []),
    ("Malayalam", 0x0D00, 0x0D7F, "ml", []),
    ("Ol Chiki", 0x1C50, 0x1C7F, "sat", []),
    ("Meitei Mayek", 0xABC0, 0xABFF, "mni", []),
    ("Arabic", 0x0600, 0x06FF, "ur", ["ks", "sd"]),
]
_NON_LATIN_RANGES = [(s, e) for _, s, e, _, _ in _SCRIPTS]


def contains_indic(text: str) -> bool:
    """True if the text has any Indian-script (non-Latin) letters. Used to stop the LLM authoring such text."""
    return any(s <= ord(c) <= e for c in text for s, e in _NON_LATIN_RANGES)


def detect_language(text: str) -> dict[str, Any]:
    """Return {language, script, confidence, ambiguous, candidates}. `language` is None for empty/unknown text."""
    counts: dict[str, int] = {}
    latin = 0
    for c in text:
        o = ord(c)
        if c.isascii():
            latin += c.isalpha()
            continue
        for name, s, e, _, _ in _SCRIPTS:
            if s <= o <= e:
                counts[name] = counts.get(name, 0) + 1
                break
    indic_total = sum(counts.values())
    if not indic_total and not latin:
        return {"language": None, "script": None, "confidence": 0.0, "ambiguous": True, "candidates": []}
    if latin >= indic_total:
        conf = 0.7 if indic_total else 0.85
        return {"language": "en", "script": "Latin", "confidence": conf, "ambiguous": False, "candidates": ["en"]}
    script = max(counts, key=counts.get)
    _, _, _, default, others = next(x for x in _SCRIPTS if x[0] == script)
    lang, ambiguous = default, bool(others)
    if script == "Devanagari" and "ळ" in text:  # retroflex LLA: Marathi / Konkani, rarely Hindi
        lang = "mr"
    if script == "Bengali" and re.search("[ৰৱ]", text):  # Assamese RA / WA
        lang, ambiguous = "as", False
    conf = round(counts[script] / max(indic_total + latin, 1), 2)
    return {"language": lang, "script": script, "confidence": conf, "ambiguous": ambiguous, "candidates": [default, *others]}


EXPECTED_SCRIPT = {
    "en": "Latin", "ks": "Arabic", "ks-Deva": "Devanagari", "sd": "Devanagari", "sd-Arab": "Arabic", "mni": "Meitei Mayek",
    "mni-Beng": "Bengali", "ur": "Arabic",
    **{d: n for n, _, _, d, _ in _SCRIPTS},
    **{o: n for n, _, _, _, others in _SCRIPTS for o in others if o not in ("ks", "sd", "mni", "ur")},
}


def script_fraction(text: str, script: str) -> float:
    """Share of letters written in `script` (Latin or an Indic/Arabic script name)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    if script == "Latin":
        return sum(c.isascii() for c in letters) / len(letters)
    rng = next(((s, e) for n, s, e, _, _ in _SCRIPTS if n == script), None)
    return 0.0 if rng is None else sum(rng[0] <= ord(c) <= rng[1] for c in letters) / len(letters)


def normalize(text: str) -> str:
    """Casefold, NFKC, strip punctuation: for comparing a transcript with an expected text."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(c if (c.isalnum() or unicodedata.category(c).startswith("M")) else " " for c in text)
    return " ".join(text.split())


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)
