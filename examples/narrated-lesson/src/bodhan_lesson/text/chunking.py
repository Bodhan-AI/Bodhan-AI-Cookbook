"""Split narration into chunks short enough for the TTS (a sentence or two, about 25 seconds)."""

from __future__ import annotations

import re

# Sentence ends in Latin, Devanagari/Bengali (danda) and Urdu scripts.
_SENTENCE_END = re.compile(r"(?<=[.?!।॥۔])\s+")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(" ".join(text.split())) if s]


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """Cut an over-long sentence at the nearest comma, otherwise the nearest space."""
    pieces: list[str] = []
    while len(sentence) > max_chars:
        window = sentence[:max_chars]
        cut = max(window.rfind(", "), window.rfind("، "), window.rfind("; "))
        if cut < max_chars // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars - 1
        pieces.append(sentence[:cut + 1].strip())
        sentence = sentence[cut + 1:].strip()
    return [*pieces, sentence] if sentence else pieces


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Pack whole sentences into chunks of at most `max_chars` characters."""
    chunks: list[str] = []
    current = ""
    for sentence in (piece for s in split_sentences(text) for piece in _split_long(s, max_chars)):
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks
