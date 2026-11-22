"""Sentence splitting and chunking for text-to-speech and translation. Plain code."""
from __future__ import annotations

import re

# Starting guesses: tune after measuring real API limits with the smoke test.
TTS_CHUNK_CHARS = 200          # Indic-Speak wants "a sentence or two" (~30 s of speech)
TRANSLATE_CHUNK_CHARS = 1200   # Indic-Translate rejects oversized input (413)

# Split after danda / ? ! ellipsis / Urdu full stop, after a period followed by whitespace,
# and at line breaks. Decimals like "3.5" and URLs are left alone.
_SPLIT = re.compile(
    r"(?<=[\u0964\u0965?!\u2026\u06d4\u061f])(?![\u0964\u0965?!\u2026\u06d4\u061f])\s*"
    r"|(?<=\.)(?!\.)\s+"
    r"|\n+"
)
_SOFT_BREAKS = ",;:\u060c\u061b\u2014"


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, keeping the end punctuation on each sentence."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = [p.strip() for p in _SPLIT.split(text)]
    return [p for p in parts if p]


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """Break one over-long sentence at a comma-like mark, else a space, else hard."""
    pieces: list[str] = []
    rest = sentence.strip()
    while len(rest) > max_chars:
        window = rest[: max_chars + 1]
        cut = max((window.rfind(ch) for ch in _SOFT_BREAKS), default=-1)
        if cut < max_chars // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars  # no break point at all: hard cut
            pieces.append(rest[:cut].strip())
            rest = rest[cut:].strip()
            continue
        pieces.append(rest[: cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def chunk_text(text: str, max_chars: int = TTS_CHUNK_CHARS) -> list[str]:
    """Pack whole sentences into chunks of at most `max_chars` characters.

    Over-long sentences are split at commas/spaces. No text is dropped.
    """
    if max_chars < 20:
        raise ValueError("max_chars must be at least 20")
    units: list[str] = []
    for sentence in split_sentences(text):
        units.extend(_split_long(sentence, max_chars) if len(sentence) > max_chars else [sentence])

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current = f"{current} {unit}"
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def translation_token_caps(text: str) -> list[int]:
    """Bound each translation response so its maximum cost is known before the call."""
    return [max(64, len(chunk) * 2) for chunk in chunk_text(text, TRANSLATE_CHUNK_CHARS)]
