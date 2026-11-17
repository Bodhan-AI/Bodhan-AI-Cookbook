"""Unicode, digit and punctuation normalization that keeps a map back to original offsets.

Every comparison in the harness runs on normalized text, but every span we report points
into the original string. `Normalized.span_to_orig` is the bridge.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿", "­"}

PUNCT_MAP = {
    "।": ".",  # devanagari danda
    "॥": ".",  # double danda
    "۔": ".",  # urdu full stop
    "᱾": ".",  # ol chiki mucaad
    "꯫": ".",  # meetei cheikhei
    "؟": "?",
    "،": ",",
    "؛": ";",
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "«": '"',
    "»": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    "…": "...",
    "·": ".",
}

NUKTA = {"़", "়", "਼", "઼", "଼", "಼"}
CANDRABINDU_TO_ANUSVARA = {"ँ": "ं", "ঁ": "ং", "ਁ": "ਂ", "ઁ": "ં", "ଁ": "ଂ"}


@dataclass(frozen=True)
class Normalized:
    text: str
    offsets: tuple[int, ...]  # offsets[i] = start of the original cluster behind normalized char i
    ends: tuple[int, ...]  # ends[i] = end of that cluster, so spans keep trailing vowel signs
    original_length: int

    def to_orig(self, i: int) -> int:
        if not self.offsets:
            return 0
        if i >= len(self.offsets):
            return self.original_length
        return self.offsets[max(i, 0)]

    def span_to_orig(self, start: int, end: int) -> tuple[int, int]:
        if end <= start:
            pos = self.to_orig(start)
            return (pos, pos)
        orig_start = self.to_orig(start)
        orig_end = self.ends[end - 1] if end - 1 < len(self.ends) else self.original_length
        return (orig_start, max(orig_end, orig_start))


def _clusters(text: str):
    """Yield (start_index, cluster): a base character plus its trailing marks (Mn/Mc/Me).

    Indic vowel signs mostly have combining class 0, so grouping by category is what lets
    NFC compose split vowels such as Bengali "ে" + "া".
    """
    start = 0
    for i in range(1, len(text) + 1):
        if i == len(text) or not unicodedata.category(text[i]).startswith("M"):
            yield start, text[start:i]
            start = i


def _map_char(ch: str, *, fold_case: bool, strip_nukta: bool, drop_punct: bool) -> str:
    if ch in ZERO_WIDTH:
        return ""
    if strip_nukta and ch in NUKTA:
        return ""
    if strip_nukta and ch in CANDRABINDU_TO_ANUSVARA:
        return CANDRABINDU_TO_ANUSVARA[ch]
    if ch.isspace():
        return " "
    if ch in PUNCT_MAP:
        mapped = PUNCT_MAP[ch]
        return "" if drop_punct else mapped
    decimal = unicodedata.decimal(ch, None)
    if decimal is not None:
        return str(decimal)
    if drop_punct and unicodedata.category(ch).startswith(("P", "S")):
        return " " if unicodedata.category(ch).startswith("P") else ""
    if fold_case:
        return ch.casefold()
    return ch


def normalize(text: str, *, fold_case: bool = False, strip_nukta: bool = False, drop_punct: bool = False) -> Normalized:
    out_chars: list[str] = []
    offsets: list[int] = []
    ends: list[int] = []
    for start, cluster in _clusters(text):
        composed = unicodedata.normalize("NFC", cluster)
        for ch in composed:
            mapped = _map_char(ch, fold_case=fold_case, strip_nukta=strip_nukta, drop_punct=drop_punct)
            for out in mapped:
                if out == " " and (not out_chars or out_chars[-1] == " "):
                    continue
                out_chars.append(out)
                offsets.append(start)
                ends.append(start + len(cluster))
    while out_chars and out_chars[-1] == " ":
        out_chars.pop()
        offsets.pop()
        ends.pop()
    return Normalized("".join(out_chars), tuple(offsets), tuple(ends), len(text))


def norm_text(text: str, **kwargs) -> str:
    return normalize(text, **kwargs).text


def fold_digits(text: str) -> str:
    """Replace any Unicode decimal digit with its ASCII digit, keeping length unchanged."""
    return "".join(str(unicodedata.decimal(ch)) if unicodedata.decimal(ch, None) is not None else ch for ch in text)
