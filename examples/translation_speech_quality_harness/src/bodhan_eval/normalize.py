"""Text cleaning that keeps track of offsets.

Two kinds of helpers:
- Length-preserving (`ascii_digits`, `mask_protected`): output has the same length as the input, so regex
  positions on the output are positions in the original text. Used by every check that reports a span.
- `normalize`: full cleaning for string comparison, with an index map back to the original.
"""

import re
import unicodedata

ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿", "­"}
PUNCT_FOLD = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", " ": " "}
SENTENCE_END = ".!?।॥؟۔"  # . ! ? danda, double danda, Arabic question mark, Urdu full stop


def ascii_digits(text: str) -> str:
    """Map every Unicode decimal digit (any script) to 0-9. Same length as the input."""
    return "".join(str(d) if (d := unicodedata.decimal(ch, None)) is not None else ch for ch in text)


_PROTECTED = re.compile(
    r"(?P<fence>^(?P<fq>```|~~~)[^\n]*\n.*?^(?P=fq)[ \t]*$)"
    r"|(?P<env>\\begin\{(?P<envname>[a-zA-Z*]+)\}.*?\\end\{(?P=envname)\})"
    r"|(?P<dmath>\$\$.+?\$\$|\\\[.+?\\\])"
    r"|(?P<imath>\\\(.+?\\\)|(?<![\\$\w])\$(?=\S)[^$\n]*?(?<=\S)\$(?!\d))"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<url>https?://[^\s)>\]]+|\b[\w.+-]+@[\w-]+\.[\w.]+)"
    r"|(?P<html></?[a-zA-Z][^<>\n]*>)",
    re.S | re.M,
)


def protected_spans(text: str) -> list[tuple[int, int, str]]:
    """LaTeX, code, URLs, e-mail addresses and HTML tags: text that must survive translation unchanged."""
    out = []
    for m in _PROTECTED.finditer(text):
        kind = next(k for k in ("fence", "env", "dmath", "imath", "code", "url", "html") if m.group(k))
        out.append((m.start(), m.end(), kind))
    return out


def mask_protected(text: str, spans=None, fill=" ") -> str:
    """Blank out protected spans, keeping length (newlines kept so line structure survives)."""
    chars = list(text)
    for s, e, _ in spans if spans is not None else protected_spans(text):
        for i in range(s, e):
            if chars[i] != "\n":
                chars[i] = fill
    return "".join(chars)


def script_of_char(ch: str) -> str | None:
    """Unicode script word for letters and marks ('DEVANAGARI', 'LATIN', 'TAMIL', ...); None otherwise."""
    if not unicodedata.category(ch).startswith(("L", "M")):
        return None
    name = unicodedata.name(ch, "")
    return name.split(" ")[0] if name else None


def normalize(text: str, lower: bool = True) -> tuple[str, list[int]]:
    """Cleaned text for comparison plus, for each output character, its index in the original.

    NFC per combining sequence, digits to 0-9, zero-width characters removed, curly punctuation folded,
    whitespace collapsed, optional lower-casing.
    """
    out, idx = [], []
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(text[j]):  # keep a base character with its marks
            j += 1
        for ch in unicodedata.normalize("NFC", text[i:j]):
            if ch in ZERO_WIDTH:
                continue
            ch = PUNCT_FOLD.get(ch, ch)
            d = unicodedata.decimal(ch, None)
            ch = str(d) if d is not None else (ch.lower() if lower else ch)
            if ch.isspace():
                if not out or out[-1] == " ":
                    continue
                ch = " "
            out.append(ch)
            idx.append(i)
        i = j
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def to_original(span: tuple[int, int], idx: list[int], original: str) -> tuple[int, int]:
    """Map a [start, end) span in normalized text back to the original text."""
    s, e = span
    if not idx or e <= s:
        p = idx[s] if idx and s < len(idx) else len(original)
        return p, p
    start, end = idx[s], idx[e - 1] + 1
    # Include combining marks and invisible characters that belong to the last character.
    while end < len(original) and (unicodedata.combining(original[end]) or original[end] in ZERO_WIDTH):
        end += 1
    return start, end


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentences as [start, end) spans: split at line breaks and at sentence-final marks followed by space."""
    spans = []
    for line in re.finditer(r"[^\n]+", text):
        start = line.start()
        for m in re.finditer(rf"[{re.escape(SENTENCE_END)}]+(?=\s|$)", line.group()):
            end = line.start() + m.end()
            spans.append((start, end))
            start = end
        spans.append((start, line.end()))
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return out
