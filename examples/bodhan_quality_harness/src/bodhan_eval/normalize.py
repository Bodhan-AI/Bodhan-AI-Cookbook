"""Script-aware normalization.

Everything downstream compares text, so this module is the foundation: Indic
digits, scripts, punctuation and whitespace all have to collapse to a canonical
form before any error rate means anything. Getting this wrong produces false
alarms, which is the fastest way to lose marks.
"""

from __future__ import annotations

import re
import unicodedata

# Language code -> the script its text must be written in.
LANG_SCRIPT: dict[str, str] = {
    "hi": "devanagari", "mr": "devanagari", "ne": "devanagari", "sa": "devanagari",
    "bn": "bengali", "as": "bengali",
    "ta": "tamil", "te": "telugu", "kn": "kannada", "ml": "malayalam",
    "gu": "gujarati", "pa": "gurmukhi", "or": "oriya", "od": "oriya",
    "ur": "arabic",
}

# (start, end) code point ranges per script.
_SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "devanagari": (0x0900, 0x097F),
    "bengali": (0x0980, 0x09FF),
    "gurmukhi": (0x0A00, 0x0A7F),
    "gujarati": (0x0A80, 0x0AFF),
    "oriya": (0x0B00, 0x0B7F),
    "tamil": (0x0B80, 0x0BFF),
    "telugu": (0x0C00, 0x0C7F),
    "kannada": (0x0C80, 0x0CFF),
    "malayalam": (0x0D00, 0x0D7F),
    "arabic": (0x0600, 0x06FF),
}

# Indic digit blocks -> ASCII, so "२५" and "25" compare equal.
_DIGIT_BLOCKS = [
    (0x0966, "devanagari"), (0x09E6, "bengali"), (0x0A66, "gurmukhi"),
    (0x0AE6, "gujarati"), (0x0B66, "oriya"), (0x0BE6, "tamil"),
    (0x0C66, "telugu"), (0x0CE6, "kannada"), (0x0D66, "malayalam"),
]


def _digit_map() -> dict[int, str]:
    m: dict[int, str] = {}
    for base, _ in _DIGIT_BLOCKS:
        for i in range(10):
            m[base + i] = str(i)
    return m


_DIGITS = _digit_map()

# Punctuation that differs across scripts but means the same thing.
_PUNCT_MAP = {
    "\u0964": ".",   # devanagari danda
    "\u0965": ".",   # double danda
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u00a0": " ",
}

_WS = re.compile(r"\s+")


def to_ascii_digits(text: str) -> str:
    """Map every Indic digit to its ASCII equivalent."""
    return text.translate(_DIGITS)


def normalize(text: str) -> str:
    """Canonical form for comparison: NFKC, digits, punctuation, whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = to_ascii_digits(text)
    text = text.translate(str.maketrans(_PUNCT_MAP))
    text = _WS.sub(" ", text)
    return text.strip()


def normalize_loose(text: str) -> str:
    """Comparison form that also drops punctuation and case.

    Used for term/entity matching, where punctuation and casing are noise.
    """
    text = normalize(text).lower()
    return re.sub(r"[^\w\u0900-\u0DFF]+", " ", text).strip()


def script_of_char(ch: str) -> str | None:
    cp = ord(ch)
    for name, (lo, hi) in _SCRIPT_RANGES.items():
        if lo <= cp <= hi:
            return name
    return None


def script_profile(text: str) -> dict[str, int]:
    """Count characters per script, ignoring spaces/punctuation/Latin."""
    counts: dict[str, int] = {}
    for ch in text:
        name = script_of_char(ch)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def dominant_script(text: str) -> str | None:
    prof = script_profile(text)
    if not prof:
        return None
    return max(prof, key=prof.get)


def expected_script(lang: str) -> str | None:
    return LANG_SCRIPT.get((lang or "").lower().split("-")[0])


def script_conformance(text: str, lang: str) -> tuple[float, str | None]:
    """Fraction of script-bearing characters in the language's own script.

    Returns (fraction, detected_script). A fraction near 0 means the output is
    in the wrong script entirely; a middling value means mixed script.
    """
    prof = script_profile(text)
    total = sum(prof.values())
    if total == 0:
        return 0.0, None
    want = expected_script(lang)
    if want is None:
        return 1.0, dominant_script(text)
    return prof.get(want, 0) / total, dominant_script(text)


def latin_tokens(text: str) -> list[str]:
    """Latin-script tokens (acronyms, brand names) that should survive translation."""
    # no trailing punctuation: "level." is the word "level" plus a full stop
    return re.findall(r"[A-Za-z](?:[A-Za-z0-9+#._-]*[A-Za-z0-9+#])?", text)


def word_count(text: str) -> int:
    return len(normalize(text).split())
