"""Lossless text splitter: ``"".join(split_text(t, n)) == t`` for any text.

Splits at sentence ends and line breaks first, so chunks rarely cut a sentence. A single sentence
longer than ``max_chars`` is split at word boundaries, and a single word longer than that is sliced.
Whitespace stays attached to the chunks, which lets callers re-join translations with the exact
original layout (paragraphs, list lines, table rows).
"""

from __future__ import annotations

import re

# Split after a sentence terminator (Latin, Devanagari danda, Urdu full stop) that is followed by
# whitespace, and after a line break when more text follows on the next line. Zero-width split points.
_BOUNDARY = re.compile(r"(?<=[.!?।॥۔])(?=\s)|(?<=\n)(?=[^\n])")
_WORD = re.compile(r"\S+\s*|\s+")


def _pieces(unit: str, max_chars: int) -> list[str]:
    """Break one over-long unit at word boundaries, then slice words that are still too long."""
    if len(unit) <= max_chars:
        return [unit]
    out: list[str] = []
    cur = ""
    for word in _WORD.findall(unit):
        if len(word) > max_chars:  # no whitespace to split on (URL, CJK, ...)
            if cur:
                out.append(cur)
                cur = ""
            out.extend(word[i : i + max_chars] for i in range(0, len(word), max_chars))
        elif len(cur) + len(word) > max_chars:
            out.append(cur)
            cur = word
        else:
            cur += word
    if cur:
        out.append(cur)
    return out


def split_text(text: str, max_chars: int) -> list[str]:
    """Split ``text`` into chunks of at most ``max_chars`` characters (lossless)."""
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    chunks: list[str] = []
    cur = ""
    for unit in _BOUNDARY.split(text):
        for piece in _pieces(unit, max_chars):
            if cur and len(cur) + len(piece) > max_chars:
                chunks.append(cur)
                cur = piece
            else:
                cur += piece
    if cur:
        chunks.append(cur)
    return chunks
