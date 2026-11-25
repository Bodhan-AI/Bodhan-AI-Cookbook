"""Language identification by script / character set (allowed by the assignment; no ML model involved).

Scripts that map to one language are exact. Shared scripts (Devanagari, Bengali, Perso-Arabic) return the
most likely language plus the other candidates, so callers can see the ambiguity. Romanized Indic text
(Latin letters) is reported as English because script alone cannot tell it apart.
"""

from __future__ import annotations

from pydantic import BaseModel

# (name, first code point, last code point, default language, all languages written in this script)
_SCRIPTS = [
    ("Devanagari", 0x0900, 0x097F, "hi", ["hi", "mr", "ne", "sa", "kok", "mai", "doi", "brx", "sd", "ks-Deva"]),
    ("Bengali", 0x0980, 0x09FF, "bn", ["bn", "as", "mni-Beng"]),
    ("Gurmukhi", 0x0A00, 0x0A7F, "pa", ["pa"]),
    ("Gujarati", 0x0A80, 0x0AFF, "gu", ["gu"]),
    ("Odia", 0x0B00, 0x0B7F, "or", ["or"]),
    ("Tamil", 0x0B80, 0x0BFF, "ta", ["ta"]),
    ("Telugu", 0x0C00, 0x0C7F, "te", ["te"]),
    ("Kannada", 0x0C80, 0x0CFF, "kn", ["kn"]),
    ("Malayalam", 0x0D00, 0x0D7F, "ml", ["ml"]),
    ("Perso-Arabic", 0x0600, 0x06FF, "ur", ["ur", "ks", "sd-Arab"]),
    ("Ol Chiki", 0x1C50, 0x1C7F, "sat", ["sat"]),
    ("Meitei Mayek", 0xABC0, 0xABFF, "mni", ["mni"]),
]

# Letters that settle an ambiguous script. Checked in order; first hit wins.
_MARKERS = [
    ("Devanagari", "mr", "ळ"),   # also used by kok, but Marathi is far more common
    ("Bengali", "as", "ৰৱ"),     # Assamese-only letters
]


class LangResult(BaseModel):
    language: str
    script: str
    confidence: float  # share of letters in the winning script, 0..1
    candidates: list[str]  # other languages written in the same script


def detect_language(text: str) -> LangResult | None:
    """Return the most likely language, or None when the text has no letters at all."""
    counts: dict[str, int] = {}
    latin = 0
    for ch in text:
        cp = ord(ch)
        if ch.isascii():
            latin += ch.isalpha()
            continue
        for name, lo, hi, _, _ in _SCRIPTS:
            if lo <= cp <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    total = sum(counts.values()) + latin
    if total == 0:
        return None
    if latin >= sum(counts.values()):
        return LangResult(language="en", script="Latin", confidence=round(latin / total, 3), candidates=["en"])
    best = max(counts, key=counts.get)
    _, _, _, default, langs = next(s for s in _SCRIPTS if s[0] == best)
    language = default
    for script, lang, letters in _MARKERS:
        if script == best and any(c in text for c in letters):
            language = lang
            break
    return LangResult(language=language, script=best, confidence=round(counts[best] / total, 3), candidates=langs)


def describe(text: str) -> dict:
    """Language fields for a tool response: detected_language / script / language_candidates (None when no letters).

    Script-based, so it is exact for scripts used by one language (Telugu, Tamil, ...) and a best guess for shared
    ones (Devanagari: Hindi vs Marathi vs Nepali). Latin letters are reported as English (romanized Indic is not told apart).
    """
    found = detect_language(text)
    if found is None:
        return {"detected_language": None, "script": None, "language_candidates": []}
    return {"detected_language": found.language, "script": found.script, "language_candidates": found.candidates}
