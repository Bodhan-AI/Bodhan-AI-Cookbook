"""Script (writing system) detection by Unicode range. Plain code, no model involved.

Script can tell Telugu from Tamil, but NOT Hindi from Marathi (both Devanagari).
`detect_script` therefore returns candidate languages and an `unambiguous` flag; when it
is False the caller must make an assumption and record it.
"""
from __future__ import annotations

import unicodedata

_RANGES = [
    ("Devanagari", 0x0900, 0x097F),
    ("Bengali", 0x0980, 0x09FF),
    ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF),
    ("Odia", 0x0B00, 0x0B7F),
    ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F),
    ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F),
    ("Perso-Arabic", 0x0600, 0x06FF),
    ("Perso-Arabic", 0x0750, 0x077F),
    ("Perso-Arabic", 0xFB50, 0xFDFF),
    ("Perso-Arabic", 0xFE70, 0xFEFF),
    ("Ol Chiki", 0x1C50, 0x1C7F),
    ("Meitei Mayek", 0xABC0, 0xABFF),
    ("Meitei Mayek", 0xAAE0, 0xAAFF),
    ("Latin", 0x0041, 0x005A),
    ("Latin", 0x0061, 0x007A),
    ("Latin", 0x00C0, 0x024F),
]

# Candidate language codes per script (codes as used by the Bodhan models).
SCRIPT_LANGUAGES = {
    "Devanagari": ["hi", "mr", "ne", "sa", "kok", "mai", "doi", "brx", "bho", "bhb", "sd", "ks-Deva"],
    "Bengali": ["bn", "as", "mni-Beng"],
    "Gurmukhi": ["pa"],
    "Gujarati": ["gu"],
    "Odia": ["or"],
    "Tamil": ["ta"],
    "Telugu": ["te"],
    "Kannada": ["kn"],
    "Malayalam": ["ml"],
    "Perso-Arabic": ["ur", "ks", "sd-Arab"],
    "Ol Chiki": ["sat"],
    "Meitei Mayek": ["mni"],
    "Latin": ["en"],
}

_LANG_TO_SCRIPT = {lang: script for script, langs in SCRIPT_LANGUAGES.items() for lang in langs}

# Letters used only in Assamese (not in standard Bengali): a strong hint for 'as'.
_ASSAMESE_LETTERS = {"\u09f0", "\u09f1"}


def language_script(language: str) -> str | None:
    """The script a language is normally written in, e.g. 'ta' -> 'Tamil'."""
    return _LANG_TO_SCRIPT.get(language)


def _script_of(ch: str) -> str | None:
    code = ord(ch)
    for name, lo, hi in _RANGES:
        if lo <= code <= hi:
            return name
    return None


def detect_script(text: str) -> dict:
    """Find the dominant script of `text`.

    Returns {script, confidence, counts, candidates, unambiguous, notes}.
    `confidence` is the dominant script's share of all letters (0..1).
    """
    counts: dict[str, int] = {}
    for ch in text or "":
        category = unicodedata.category(ch)
        if category[0] not in ("L", "M"):
            continue
        script = _script_of(ch)
        if script:
            counts[script] = counts.get(script, 0) + 1
    total = sum(counts.values())
    if not total:
        return {"script": None, "confidence": 0.0, "counts": {}, "candidates": [],
                "unambiguous": False, "notes": ["No letters in a known script were found."]}

    script = max(counts, key=lambda k: counts[k])
    confidence = counts[script] / total
    candidates = list(SCRIPT_LANGUAGES[script])
    notes: list[str] = []

    if script == "Bengali" and any(ch in _ASSAMESE_LETTERS for ch in text):
        candidates = ["as"] + [c for c in candidates if c != "as"]
        notes.append("Contains Assamese-only letters, so 'as' is the most likely language.")
    if script == "Latin":
        notes.append("Latin script may be English or romanised Indian-language text.")
    if len(candidates) > 1 and script == "Devanagari":
        notes.append("Devanagari is shared by many languages (Hindi, Marathi, Nepali...); "
                     "script alone cannot decide. State an assumption.")
    if confidence < 0.6:
        notes.append("Mixed scripts: no single script dominates.")

    return {
        "script": script,
        "confidence": round(confidence, 3),
        "counts": counts,
        "candidates": candidates,
        "unambiguous": len(candidates) == 1 and confidence >= 0.6,
        "notes": notes,
    }


def script_matches_language(text: str, language: str, min_fraction: float = 0.6) -> bool:
    """True if at least `min_fraction` of the letters are in `language`'s usual script."""
    expected = language_script(language)
    if expected is None:
        return True  # unknown language: cannot judge
    info = detect_script(text)
    total = sum(info["counts"].values())
    if not total:
        return True  # nothing to judge (numbers, punctuation)
    return info["counts"].get(expected, 0) / total >= min_fraction
