"""Text normalization for comparisons, with a map back to the original text.

Checks compare normalized copies ("२५" vs "25", "।" vs ".", Unicode variants), but every flag must
point at the original string, so `Normalized.to_original` turns a span in the normalized text back
into a span in the input.

Two strengths:
- default: Unicode NFC, invisible characters removed, native digits -> ASCII, digit-group commas
  removed (1,00,000 and 100,000 -> 100000), punctuation variants unified, whitespace collapsed.
  Case and all letters are kept, so entity and number checks can use it.
- loose=True: also case-folded, punctuation and symbols turned into spaces, nukta and candrabindu
  folded. For comparing speech transcripts with the text that was spoken: the transcriber drops
  punctuation and spells these marks inconsistently.

Language-independent on purpose: everything is driven by Unicode properties, so an unseen language
needs no code change.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Zero-width and bidi controls, BOM, soft hyphen: invisible, and they block comparison.
_INVISIBLE = set("​‌‍⁠﻿­‎‏") | {chr(c) for c in range(0x202A, 0x202F)} | {
    chr(c) for c in range(0x2066, 0x206A)
}

_CHAR_MAP = {
    # Sentence ends and Arabic-script punctuation (Urdu, Kashmiri, Sindhi)
    "।": ".", "॥": ".", "۔": ".", "؟": "?", "،": ",", "؛": ";",
    "٫": ".", "٬": ",",  # Arabic decimal and thousands separators
    # Quotes, dashes, minus, ellipsis
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"', "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "…": "...",
    # Atomic letters that are also written as consonant + virama (+ the ZWJ we remove):
    # Bengali khanda ta, Malayalam chillus.
    "ৎ": "ত্",
    "ൺ": "ണ്", "ൻ": "ന്", "ർ": "ര്",
    "ൽ": "ല്", "ൾ": "ള്", "ൿ": "ക്",
}
# ponytail: known spelling variants beyond these (e.g. Devanagari chandra vowels) are not folded;
# add them here when calibration shows false alarms from them.

# Nukta signs and candrabindu -> anusvara, for every Indic script, found by Unicode name.
_NUKTAS: set[str] = set()
_CANDRABINDU: dict[str, str] = {}
for _cp in range(0x0900, 0x0E00):
    _name = unicodedata.name(chr(_cp), "")
    if _name.endswith("SIGN NUKTA"):
        _NUKTAS.add(chr(_cp))
    elif _name.endswith("SIGN CANDRABINDU"):
        try:
            _CANDRABINDU[chr(_cp)] = unicodedata.lookup(_name.replace("CANDRABINDU", "ANUSVARA"))
        except KeyError:
            pass

# Digit groups: Western 1,000,000 or Indian 1,00,000 / 12,34,567. Lists like "10,20,30" or "2019,2020" are left alone.
_GROUPED_NUMBER = re.compile(r"(?<![\d,])(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3})(?!\d|,\d)")

# Each character of the text being built carries the [start, end) of the original text it came from.
_Chars = list[tuple[str, int, int]]


@dataclass(frozen=True)
class Normalized:
    text: str
    spans: tuple[tuple[int, int], ...]  # original [start, end) for each character of `text`
    original_length: int

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """Span in the normalized text -> span in the original text. An empty span is an insertion point."""
        if start >= end:
            pos = self.spans[start][0] if start < len(self.spans) else self.original_length
            return pos, pos
        return self.spans[start][0], self.spans[end - 1][1]


def normalize(text: str, *, loose: bool = False) -> Normalized:
    chars = _nfc([(c, i, i + 1) for i, c in enumerate(text) if c not in _INVISIBLE])
    chars = [(o, s, e) for c, s, e in chars for o in _base_char(c)]
    chars = _drop_group_commas(chars)
    if loose:
        chars = [(o, s, e) for c, s, e in chars for o in _loose_char(c)]
    chars = _collapse_spaces(chars)
    return Normalized("".join(c for c, _, _ in chars), tuple((s, e) for _, s, e in chars), len(text))


def _nfc(chars: _Chars) -> _Chars:
    """NFC one cluster at a time (a base character plus its following marks), so positions stay traceable.

    Indic vowel signs are marks, so two-part vowels (Bengali ে + া -> ো) compose inside their cluster.
    """
    out: _Chars = []
    i = 0
    while i < len(chars):
        j = i + 1
        while j < len(chars) and unicodedata.category(chars[j][0]).startswith("M"):
            j += 1
        cluster = chars[i:j]
        part = "".join(c for c, _, _ in cluster)
        composed = unicodedata.normalize("NFC", part)
        # Characters before the first change keep their exact positions; the rewritten tail maps to what it replaced.
        k = 0
        while k < min(len(part), len(composed)) and part[k] == composed[k]:
            k += 1
        out.extend(cluster[:k])
        if k < len(composed):
            start, end = cluster[k][1], cluster[-1][2]
            out.extend((c, start, end) for c in composed[k:])
        i = j
    return out


def _base_char(c: str) -> str:
    if c in _CHAR_MAP:
        return _CHAR_MAP[c]
    if c.isspace():
        return " "
    if unicodedata.category(c) == "Nd":  # every script's native digits, with no per-language table
        return str(unicodedata.digit(c))
    return c


def _loose_char(c: str) -> str:
    if c in _NUKTAS:
        return ""
    if c in _CANDRABINDU:
        return _CANDRABINDU[c]
    if unicodedata.category(c)[0] in "PS":  # punctuation and symbols become word breaks
        return " "
    return c.casefold()


def _drop_group_commas(chars: _Chars) -> _Chars:
    text = "".join(c for c, _, _ in chars)
    drop = {m.start() + k for m in _GROUPED_NUMBER.finditer(text) for k, ch in enumerate(m.group()) if ch == ","}
    return [ch for i, ch in enumerate(chars) if i not in drop]


def _collapse_spaces(chars: _Chars) -> _Chars:
    out: _Chars = []
    for ch in chars:
        if ch[0] == " " and (not out or out[-1][0] == " "):
            continue
        out.append(ch)
    if out and out[-1][0] == " ":
        out.pop()
    return out
