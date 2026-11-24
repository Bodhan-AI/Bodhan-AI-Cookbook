"""Deterministic text checks on translations. Pure functions, no model calls.

Code can't judge meaning, but it can check facts that must survive translation:
the right script, the same numbers, and every glossary term present in the
English back-translation.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Unicode blocks of each target language's native script.
_DEVANAGARI = [(0x0900, 0x097F), (0xA8E0, 0xA8FF)]
_ARABIC = [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)]
SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    **{lang: _DEVANAGARI for lang in ("hi", "mr", "ne", "sa", "mai", "doi", "kok", "brx", "sd")},
    "bn": [(0x0980, 0x09FF)], "as": [(0x0980, 0x09FF)],
    "pa": [(0x0A00, 0x0A7F)], "gu": [(0x0A80, 0x0AFF)], "or": [(0x0B00, 0x0B7F)],
    "ta": [(0x0B80, 0x0BFF)], "te": [(0x0C00, 0x0C7F)], "kn": [(0x0C80, 0x0CFF)],
    "ml": [(0x0D00, 0x0D7F)], "ur": _ARABIC, "ks": _ARABIC,
    "sat": [(0x1C50, 0x1C7F)], "mni": [(0xABC0, 0xABFF)],
}

# Zero of each target language's native digits. Indic-Speak reads ASCII digits in English even
# inside Hindi or Tamil ("180" -> "one hundred and eighty") but native digits in the language
# itself (१८० -> एक सौ अस्सी); verified live for all 22 target languages (2026-11-22).
NATIVE_ZERO: dict[str, int] = {
    **{lang: 0x0966 for lang in ("hi", "mr", "ne", "sa", "mai", "doi", "kok", "brx", "sd")},  # Devanagari
    "bn": 0x09E6, "as": 0x09E6, "pa": 0x0A66, "gu": 0x0AE6, "or": 0x0B66, "ta": 0x0BE6, "te": 0x0C66,
    "kn": 0x0CE6, "ml": 0x0D66, "ur": 0x06F0, "ks": 0x06F0, "sat": 0x1C50, "mni": 0xABF0,
}
# Not touching a Latin letter, directly or across a hyphen ("COVID-19", "19-B" are names).
# Whole runs only: the digit guards stop a match starting or ending inside "19".
_DIGIT_RUN = re.compile(r"(?<![0-9A-Za-z])(?<![A-Za-z]-)[0-9]+(?![0-9A-Za-z])(?!-[A-Za-z])")


def native_digits(text: str, lang: str) -> str:
    """Write ASCII digit runs in `lang`'s own digits, for speech only (subtitles keep the translation).

    Runs touching Latin letters ("H2O", "COVID-19", "x2") are left alone: those are names, not numbers.
    """
    zero = NATIVE_ZERO.get(lang)
    if zero is None:
        return text
    return _DIGIT_RUN.sub(lambda m: "".join(chr(zero + int(d)) for d in m.group()), text)


_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def script_share(text: str, lang: str) -> float:
    """Share of letters (and combining marks) that are in `lang`'s native script."""
    ranges = SCRIPT_RANGES.get(lang)
    letters = [ch for ch in text if unicodedata.category(ch)[0] in "LM"]
    if not ranges or not letters:
        return 0.0 if letters or ranges else 1.0
    native = sum(any(lo <= ord(ch) <= hi for lo, hi in ranges) for ch in letters)
    return native / len(letters)


def ascii_digits(text: str) -> str:
    """'১৮০' -> '180', '२५' -> '25': every Unicode decimal digit becomes 0-9."""
    return "".join(str(d) if (d := unicodedata.decimal(ch, None)) is not None else ch for ch in text)


def numbers(text: str) -> Counter[str]:
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", ascii_digits(text))  # 1,000 -> 1000
    return Counter(_NUMBER.findall(text))


def missing_numbers(source: str, translation: str) -> list[str]:
    """Numbers in the source that the translation lost (counted with multiplicity)."""
    return sorted((numbers(source) - numbers(translation)).elements())


def _term_pattern(term: str, kind: str) -> re.Pattern[str]:
    if kind == "label" and term.isupper() and term.isalpha():
        # Back-translations sometimes space a label out: "XYZ" -> "X Y Z" or "X-Y-Z".
        body = r"[\s-]?".join(map(re.escape, term))
        return re.compile(rf"(?<![A-Za-z]){body}(?![A-Za-z])")
    words = re.split(r"[\s-]+", term.strip())
    # Hyphen or space between words; singular or plural on any word ("arms" ~ "arm", "angle" ~ "angles").
    stems = [w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 3 else w for w in words]
    body = r"[\s-]+".join(rf"{re.escape(w)}(?:e?s)?" for w in stems)
    return re.compile(rf"(?<!\w){body}(?!\w)", 0 if kind == "label" else re.IGNORECASE)


def term_in(term: str, kind: str, text: str) -> bool:
    return bool(_term_pattern(term, kind).search(text))


def checkable_terms(terms: list[tuple[str, str]], source: str) -> list[tuple[str, str]]:
    """Glossary terms present in this source sentence. Single letters are too ambiguous to check."""
    return [(t, k) for t, k in terms if len(t.replace(" ", "")) >= 2 and term_in(t, k, source)]


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s]
