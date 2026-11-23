"""Unicode and Indic-script normalisation, with offset maps back to the original text.

Every comparison in the harness (numbers, ASR vs text, back-translation vs
source) runs on normalised text, but every span we report must point into the
text as the user supplied it. ``normalize_with_map`` therefore returns, for each
output character, the [start, end) of the original characters it came from.

Steps (each optional):
  * NFC per grapheme cluster (so क़ written as one code point or as क + nukta compare equal)
  * drop zero-width characters (ZWJ, ZWNJ, ZWSP, BOM, soft hyphen)
  * any Unicode decimal digit -> ASCII (२५ -> 25, ௨௫ -> 25, ২৫ -> 25)
  * unify quotes, dashes, danda/full stops, exotic spaces
  * casefold
  * ``asr_fold``: drop nukta and map candrabindu to anusvara, two spelling
    variations ASR output is not reliable about
  * collapse runs of whitespace
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿", "­"}
PUNCT_MAP = {
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
    "।": ".",
    "॥": ".",
    "۔": ".",
    "؟": "?",
    "،": ",",
    "。": ".",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
}
# Nukta signs of the Brahmi-derived scripts, and candrabindu -> anusvara pairs.
NUKTAS = {"़", "়", "਼", "઼", "଼", "఼", "಼"}
CANDRABINDU = {"ँ": "ं", "ঁ": "ং", "ਁ": "ਂ", "ઁ": "ં", "ଁ": "ଂ", "ఁ": "ం", "ಁ": "ಂ", "ഁ": "ം"}
SENTENCE_END = ".!?।॥؟۔"


def _is_mark(ch: str) -> bool:
    return unicodedata.category(ch).startswith("M")


def clusters(text: str):
    """Yield (start, end) of base-character + combining-mark clusters."""
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and (_is_mark(text[j]) or text[j] in ("‌", "‍") and j + 1 < n and _is_mark(text[j + 1])):
            j += 1
        yield i, j
        i = j


@dataclass
class Normalized:
    text: str
    starts: list[int]  # original start offset for each output char
    ends: list[int]  # original end offset for each output char

    def to_original(self, a: int, b: int) -> tuple[int, int]:
        """Map a [a, b) span in normalised text to the original text."""
        if not self.text:
            return (0, 0)
        a = min(max(a, 0), len(self.text) - 1)
        b = min(max(b, a + 1), len(self.text))
        return self.starts[a], self.ends[b - 1]


def normalize_with_map(
    text: str,
    *,
    digits: bool = True,
    zero_width: bool = True,
    punct: bool = True,
    casefold: bool = True,
    asr_fold: bool = False,
    collapse_space: bool = True,
) -> Normalized:
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for s, e in clusters(text or ""):
        piece = unicodedata.normalize("NFC", text[s:e])
        for ch in piece:
            if zero_width and ch in ZERO_WIDTH:
                continue
            if asr_fold and ch in NUKTAS:
                continue
            if asr_fold and ch in CANDRABINDU:
                ch = CANDRABINDU[ch]
            if digits and unicodedata.category(ch) == "Nd":
                ch = str(unicodedata.decimal(ch, 0))
            if punct and ch in PUNCT_MAP:
                ch = PUNCT_MAP[ch]
            if ch.isspace():
                if collapse_space and out and out[-1] == " ":
                    ends[-1] = e
                    continue
                ch = " "
            emitted = ch.casefold() if casefold else ch
            for c in emitted:
                out.append(c)
                starts.append(s)
                ends.append(e)
    return Normalized("".join(out), starts, ends)


def normalize_text(text: str, **kw) -> str:
    return normalize_with_map(text, **kw).text.strip()


def ascii_digits(text: str) -> str:
    """Replace every Unicode decimal digit by its ASCII digit. Length-preserving, so offsets stay valid."""
    return "".join(str(unicodedata.decimal(c, 0)) if unicodedata.category(c) == "Nd" else c for c in text)


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int


def _is_word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in "LMN" or ch in ("‌", "‍")


def word_tokens(text: str) -> list[Token]:
    """Words = maximal runs of letters, combining marks, digits and ZWJ/ZWNJ (works for every Indic script)."""
    toks: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        if _is_word_char(text[i]):
            j = i + 1
            while j < n and (
                _is_word_char(text[j])
                or (text[j] in ".,'" and j + 1 < n and text[j + 1].isdigit() and text[j - 1].isdigit())
            ):
                j += 1
            toks.append(Token(text[i:j], i, j))
            i = j
        else:
            i += 1
    return toks


def ws_tokens(text: str) -> list[Token]:
    """Whitespace tokens with offsets; what the judge sees, numbered."""
    return [Token(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def segments(text: str) -> list[tuple[int, int]]:
    """Split into sentence-like segments: every non-empty line, then sentence enders followed by space."""
    spans: list[tuple[int, int]] = []
    for line in re.finditer(r"[^\n]+", text):
        ls, le = line.start(), line.end()
        chunk = text[ls:le]
        if not chunk.strip():
            continue
        start = 0
        for m in re.finditer(rf"[{re.escape(SENTENCE_END)}]+[\"'”’)]*(?=\s+\S)", chunk):
            prev = chunk[m.start() - 1] if m.start() > 0 else ""
            nxt = chunk[m.end() : m.end() + 2].strip()[:1]
            if m.group(0)[0] == "." and prev.isdigit() and nxt.isdigit():
                continue  # decimal point
            spans.append((ls + start, ls + m.end()))
            start = m.end()
        spans.append((ls + start, le))
    out = []
    for a, b in spans:
        seg = text[a:b]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg) - len(seg.rstrip())
        if b - trail > a + lead:
            out.append((a + lead, b - trail))
    return out


# ---------------------------------------------------------------------------
# English content words (for comparing the source with back-translations)
# ---------------------------------------------------------------------------

STOPWORDS = frozenset(
    """a an the and or but if then so of to in on at by for from with without into onto over under about as is are was were be been
    being am do does did done have has had having this that these those it its it's they them their there here he she him her his
    we us our you your i me my mine not no nor can could will would shall should may might must also very just only than too
    which who whom whose what when where why how all any both each few more most other some such own same s t up down out off
    again further once while during before after above below between through until because per via""".split()
)
# Small, deliberately conservative suffix stripper: enough to equate "boils"/"boil"/"boiling",
# "times"/"time", "stopped"/"stop". Not a linguistic stemmer; both sides go through the same function.
_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "ied", "ed", "ly", "es", "s")


def stem(word: str) -> str:
    w = word.lower()
    for suf in _SUFFIXES:
        if len(w) > len(suf) + 2 and w.endswith(suf):
            if suf == "es" and not w[:-2].endswith(("s", "x", "z", "ch", "sh")):
                continue  # "times" -> strip only "s" below
            if suf == "s" and w.endswith("ss"):
                break
            w = w[: -len(suf)]
            if suf in ("ies", "ied"):
                w += "y"
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":
        w = w[:-1]  # stopp -> stop, runn -> run
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]  # time -> tim, so "time"/"times"/"timed" agree
    return w


ENGLISH_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
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
    "million": 1000000,
    "billion": 1000000000,
    "lakh": 100000,
    "crore": 10000000,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "half": 0.5,
    "twice": 2,
    "double": 2,
    "dozen": 12,
}


def content_words(text: str) -> list[tuple[str, int, int]]:
    """Lower-cased, stemmed content words of English text with offsets; numbers kept as canonical strings."""
    out = []
    for m in re.finditer(r"[A-Za-z]+(?:'[a-z]+)?|\d+(?:[.,]\d+)*", ascii_digits(text)):
        w = m.group(0)
        if w[0].isdigit():
            out.append((canonical_number(w), m.start(), m.end()))
            continue
        lw = w.lower()
        if lw in STOPWORDS or len(lw) < 2:
            continue
        if lw in ENGLISH_NUMBER_WORDS:
            out.append((canonical_number(str(ENGLISH_NUMBER_WORDS[lw])), m.start(), m.end()))
            continue
        out.append((stem(lw), m.start(), m.end()))
    return out


def canonical_number(s: str) -> str:
    """'1,00,000' -> '100000', '3.50' -> '3.5', '007' -> '7'. Commas are treated as grouping separators."""
    s = ascii_digits(s).replace(",", "").replace("٬", "")
    if s.count(".") > 1:
        return s
    try:
        if "." in s:
            whole, frac = s.split(".")
            frac = frac.rstrip("0")
            whole = whole.lstrip("0") or "0"
            return f"{whole}.{frac}" if frac else whole
        return s.lstrip("0") or "0"
    except ValueError:
        return s
