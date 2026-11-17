"""Script-independent number extraction. Native digits are folded to ASCII first."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import regex

from .normalize import normalize

NUMBER_RE = regex.compile(r"\d+(?:,\d{2,3})*(?:\.\d+)?")
GROUPED_RE = regex.compile(r"^\d{1,3}(?:,\d{2,3})+$")

EN_NUMBER_WORDS = {
    "zero": 0,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1000,
    "dozen": 12,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "tenth": 10,
    "half": 0.5,
    "one": 1,
}
SCALE_WORDS = ("hundred", "thousand", "million", "billion", "trillion", "lakh", "lakhs", "crore", "crores")


@dataclass(frozen=True)
class NumberMention:
    value: str
    start: int
    end: int
    raw: str


def canonical(raw: str) -> str:
    text = raw.replace(",", "") if GROUPED_RE.match(raw) else raw
    if "," in text:
        text = text.replace(",", "")
    if "." in text:
        whole, frac = text.split(".", 1)
        whole = whole.lstrip("0") or "0"
        frac = frac.rstrip("0")
        return f"{whole}.{frac}" if frac else whole
    return text.lstrip("0") or "0"


def extract_numbers(text: str) -> list[NumberMention]:
    norm = normalize(text)
    out = []
    for m in NUMBER_RE.finditer(norm.text):
        raw = m.group(0)
        if "," in raw and not GROUPED_RE.match(raw):
            # "3,4" is a list of two numbers, not a decimal.
            offset = m.start()
            for part in raw.split(","):
                if part:
                    s, e = norm.span_to_orig(offset, offset + len(part))
                    out.append(NumberMention(canonical(part), s, e, part))
                offset += len(part) + 1
            continue
        s, e = norm.span_to_orig(m.start(), m.end())
        out.append(NumberMention(canonical(raw), s, e, raw))
    return out


def english_number_words(text: str) -> set[str]:
    found = set()
    for m in regex.finditer(r"[A-Za-z]+", text):
        value = EN_NUMBER_WORDS.get(m.group(0).lower())
        if value is not None:
            found.add(canonical(str(value)))
    return found


def has_scale_word(text: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in SCALE_WORDS)


def multiset(mentions: list[NumberMention]) -> Counter:
    return Counter(m.value for m in mentions)


def scaled_match(a: str, b: str) -> bool:
    """True when a and b differ by a power of ten (3 million vs 30 lakh)."""
    try:
        fa, fb = float(a), float(b)
    except ValueError:
        return False
    if fa == 0 or fb == 0:
        return False
    ratio = max(fa, fb) / min(fa, fb)
    return any(abs(ratio - 10**k) < 1e-9 for k in range(1, 8))
