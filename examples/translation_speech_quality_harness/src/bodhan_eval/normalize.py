"""Unicode / Indic-script normalization with offset-preserving tokenization.

Tokens keep the (start, end) offsets of the ORIGINAL text; only the token string
is normalized. This keeps every flagged span valid for the text the user supplied.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ZERO_WIDTH = {"\u200b", "‌", "‍", "⁠", "﻿"}
_JOINERS = {"‌", "‍"}  # live inside words in Indic scripts

# (name, lo, hi) Unicode blocks. Unknown language codes still work: see expected_script().
SCRIPT_RANGES = {
    "Deva": (0x0900, 0x097F),
    "Beng": (0x0980, 0x09FF),
    "Guru": (0x0A00, 0x0A7F),
    "Gujr": (0x0A80, 0x0AFF),
    "Orya": (0x0B00, 0x0B7F),
    "Taml": (0x0B80, 0x0BFF),
    "Telu": (0x0C00, 0x0C7F),
    "Knda": (0x0C80, 0x0CFF),
    "Mlym": (0x0D00, 0x0D7F),
    "Arab": (0x0600, 0x06FF),
}
LANG_SCRIPT = {
    "hi": "Deva", "mr": "Deva", "ne": "Deva", "sa": "Deva", "kok": "Deva", "mai": "Deva",
    "bn": "Beng", "as": "Beng",
    "pa": "Guru", "gu": "Gujr", "or": "Orya", "od": "Orya",
    "ta": "Taml", "te": "Telu", "kn": "Knda", "ml": "Mlym",
    "ur": "Arab", "sd": "Arab", "ks": "Arab",
}

_PUNCT_MAP = str.maketrans(
    {
        "।": ".", "॥": ".", "۔": ".", "،": ",", "؛": ";", "؟": "?",
        "“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-",
        "…": "...", " ": " ",
    }
)


def ascii_digits(s: str) -> str:
    """Map every Unicode decimal digit (Devanagari, Bengali, Tamil, Arabic-Indic...) to ASCII.
    Length-preserving, so offsets stay valid."""
    return "".join(
        str(unicodedata.decimal(c)) if (not c.isascii() and unicodedata.category(c) == "Nd") else c
        for c in s
    )


def norm_token(tok: str) -> str:
    t = unicodedata.normalize("NFC", tok)
    t = "".join(c for c in t if c not in ZERO_WIDTH)
    return ascii_digits(t).casefold()


def norm_punct(s: str) -> str:
    return s.translate(_PUNCT_MAP)


def _is_tok_char(c: str) -> bool:
    return unicodedata.category(c)[0] in "LMN" or c in _JOINERS


@dataclass(frozen=True)
class Token:
    text: str   # normalized
    start: int  # offsets in the original string
    end: int
    raw: str


_NUM_JOIN = re.compile(r"(?<=\d)[,.](?=\d)")


def tokenize(s: str) -> list[Token]:
    toks: list[Token] = []
    # a separator inside a number (8,849 / 3.14) is swapped for a zero-width joiner of the same
    # length, so the number stays ONE token and offsets are unchanged
    scan = _NUM_JOIN.sub("‍", s)
    i, n = 0, len(s)
    while i < n:
        if _is_tok_char(scan[i]):
            j = i
            while j < n and _is_tok_char(scan[j]):
                j += 1
            raw = s[i:j]
            nt = norm_token(scan[i:j])  # joiner (stripped) makes 8,849 -> 8849
            if nt:
                toks.append(Token(nt, i, j, raw))
            i = j
        else:
            i += 1
    return toks


def normalize_text(s: str) -> str:
    return " ".join(t.text for t in tokenize(s))


def script_of(c: str) -> str | None:
    o = ord(c)
    for name, (lo, hi) in SCRIPT_RANGES.items():
        if lo <= o <= hi:
            return name
    if ("a" <= c <= "z") or ("A" <= c <= "Z"):
        return "Latn"
    return None


def expected_script(lang: str) -> str | None:
    return LANG_SCRIPT.get(lang.lower().split("-")[0])


def has_letters(s: str) -> bool:
    return any(unicodedata.category(c)[0] == "L" for c in s)


_MASK_PATTERNS = [
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"`[^`\n]*`"),
    re.compile(r"\$\$.*?\$\$", re.DOTALL),
    re.compile(r"\$[^$\n]+\$"),
    re.compile(r"\\\(.*?\\\)", re.DOTALL),
    re.compile(r"\\\[.*?\\\]", re.DOTALL),
    re.compile(r"https?://\S+"),
    re.compile(r"</?[A-Za-z][^>]*>"),
    re.compile(r"(?<=\])\([^)\n]*\)"),
]


def mask_markup(s: str) -> str:
    """Blank out code, math, URLs and HTML (same length) so script/language checks ignore them."""
    out = s
    for pat in _MASK_PATTERNS:
        out = pat.sub(lambda m: " " * (m.end() - m.start()), out)
    return out
