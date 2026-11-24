"""Unicode / Indic normalization that remembers where every output character came from.

Comparing "२५" with "25", or a precomposed nukta letter with its decomposed form, is only
meaningful after normalization, but flags must point at the *original* text. So every
normalized string carries `idx`: idx[i] is the index in the original string of output char i.

Levels
* "strict": canonical decomposition, digits -> ASCII, zero-width chars dropped, danda -> ".",
  quotes/dashes/spaces unified. Used for numbers, format and script checks.
* "speech": strict + punctuation and symbols removed, lower-cased, nukta dropped, candrabindu
  -> anusvara, whitespace collapsed. Used to compare ASR output with the text that was spoken,
  where these differences are pronunciation-neutral.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import regex

ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿", "­"}
PUNCT_MAP = {
    "।": ".", "॥": ".",  # danda, double danda
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "　": " ",
    "…": "...",
}
# Nukta signs of the major Indic blocks; candrabindu -> anusvara pairs.
NUKTAS = {"़", "়", "਼", "઼", "଼", "಼", "఼"}
CANDRABINDU = {"ँ": "ं", "ঁ": "ং", "ઁ": "ં", "ଁ": "ଂ", "ఁ": "ం"}

WORD_RE = regex.compile(r"[\p{L}\p{M}\p{N}‌‍]+")


@dataclass
class Norm:
    text: str
    idx: list[int]  # original index of each char in `text`
    original: str

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """Map a [start, end) span in normalized text back to the original string."""
        if not self.idx:
            return (0, 0)
        start = min(max(start, 0), len(self.idx) - 1)
        end = min(max(end, start + 1), len(self.idx))
        return self.idx[start], self.idx[end - 1] + 1


def _digit(ch: str) -> str | None:
    if unicodedata.category(ch) == "Nd":
        try:
            return str(unicodedata.decimal(ch))
        except ValueError:
            return None
    return None


def normalize(text: str, level: str = "strict") -> Norm:
    out: list[str] = []
    idx: list[int] = []
    speech = level == "speech"
    for i, ch in enumerate(text):
        if ch in ZERO_WIDTH:
            continue
        for c in unicodedata.normalize("NFD", ch):
            d = _digit(c)
            if d is not None:
                c = d
            c = PUNCT_MAP.get(c, c)
            for c2 in c:
                if speech:
                    if c2 in NUKTAS:
                        continue
                    c2 = CANDRABINDU.get(c2, c2)
                    cat = unicodedata.category(c2)
                    if cat[0] in "PS" or c2 in "\"'":
                        c2 = " "
                    c2 = c2.lower()
                    if c2.isspace():
                        if not out or out[-1] == " ":
                            continue
                        c2 = " "
                out.append(c2)
                idx.append(i)
    if speech:
        while out and out[-1] == " ":
            out.pop()
            idx.pop()
    return Norm("".join(out), idx, text)


@dataclass
class Token:
    text: str
    start: int  # offsets into the ORIGINAL string
    end: int


def tokenize(text: str, level: str = "strict") -> list[Token]:
    """Word tokens (letters, combining marks, digits) with original-string offsets."""
    n = normalize(text, level)
    toks = []
    for m in WORD_RE.finditer(n.text):
        s, e = n.to_original(m.start(), m.end())
        toks.append(Token(m.group(0), s, e))
    return toks


def script_of(ch: str) -> str:
    """Unicode script name of a character (e.g. 'Devanagari', 'Latin'), via the regex module."""
    for name in _SCRIPTS:
        if _SCRIPT_RE[name].match(ch):
            return name
    return "Other"


_SCRIPTS = ("Latin", "Devanagari", "Bengali", "Gujarati", "Gurmukhi", "Kannada", "Malayalam", "Oriya", "Tamil", "Telugu", "Arabic",
            "Ol_Chiki", "Meetei_Mayek", "Sinhala", "Cyrillic", "Han")
_SCRIPT_RE = {name: regex.compile(rf"\p{{Script={name}}}") for name in _SCRIPTS}


def sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans in the original text, split after . ? ! । ॥ and newlines."""
    spans, start = [], 0
    for m in regex.finditer(r"[.?!।॥]+[\"')\]]*(?=\s|$)|\n+", text):
        end = m.end()
        if text[start:end].strip():
            spans.append(_trim(text, start, end))
        start = end
    if text[start:].strip():
        spans.append(_trim(text, start, len(text)))
    return spans


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e
