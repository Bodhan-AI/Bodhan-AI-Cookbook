"""Number extraction on normalized text, plus a small English number-word parser.

Numbers are compared as canonical strings: grouping commas removed (both 1,00,000 and
100,000 -> 100000), leading zeros dropped, decimals kept ("3.50" -> "3.5").
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import regex

from .normalize import normalize

NUM_RE = regex.compile(r"(?<![\p{N}])\d+(?:[,]\d{2,3})*(?:\.\d+)?(?![\p{N}])")


@dataclass
class Num:
    value: str  # canonical
    start: int  # original-string offsets
    end: int
    raw: str


def canonical(raw: str) -> str:
    s = raw.replace(",", "")
    if "." in s:
        whole, frac = s.split(".", 1)
        frac = frac.rstrip("0")
        whole = whole.lstrip("0") or "0"
        return f"{whole}.{frac}" if frac else whole
    return s.lstrip("0") or "0"


def extract_numbers(text: str) -> list[Num]:
    n = normalize(text, "strict")
    out = []
    for m in NUM_RE.finditer(n.text):
        # a trailing ",dd" right before a non-digit is usually a list ("2,3 and 5") only if groups look odd;
        # Indian (2-digit) and Western (3-digit) grouping are both accepted.
        s, e = n.to_original(m.start(), m.end())
        out.append(Num(canonical(m.group(0)), s, e, text[s:e]))
    return out


_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100_000, "lakhs": 100_000, "million": 1_000_000, "crore": 10_000_000,
           "crores": 10_000_000, "billion": 1_000_000_000}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
             "eleventh": 11, "twelfth": 12, "twentieth": 20, "hundredth": 100}
_SPECIAL = {"dozen": 12, "half": 0.5, "twice": 2, "double": 2}


def english_number_values(text: str) -> Counter:
    """Canonical values of digits AND spelled-out numbers in English text ("two hundred" -> 200)."""
    vals: Counter = Counter(n.value for n in extract_numbers(text))
    words = regex.findall(r"[a-z]+", text.lower())
    i = 0
    while i < len(words):
        w = words[i]
        if w in _UNITS or w in _SCALES:
            total, current, j = 0, 0, i
            while j < len(words) and (words[j] in _UNITS or words[j] in _SCALES or (words[j] == "and" and j > i)):
                wj = words[j]
                if wj in _UNITS:
                    current += _UNITS[wj]
                elif wj in _SCALES:
                    scale = _SCALES[wj]
                    current = max(current, 1) * scale
                    if scale >= 1000:
                        total += current
                        current = 0
                j += 1
            vals[str(total + current)] += 1
            i = j
            continue
        if w in _ORDINALS:
            vals[str(_ORDINALS[w])] += 1
        elif w in _SPECIAL and isinstance(_SPECIAL[w], int):
            vals[str(_SPECIAL[w])] += 1
        i += 1
    return vals
