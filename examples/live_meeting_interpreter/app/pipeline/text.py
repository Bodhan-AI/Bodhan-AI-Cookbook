from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?।॥])\s+")
_SOFT_BREAK = re.compile(r"(?<=[,;:،])\s+")


def is_meaningful(text: str) -> bool:
    """Skip empty / punctuation-only transcripts (noise, coughs)."""
    return sum(ch.isalnum() for ch in text) >= 2


def split_for_tts(text: str, max_chars: int = 180) -> list[str]:
    """Bodhan TTS wants a sentence or two per request and does not split for you.

    Split on sentence ends (incl. Devanagari danda), merge tiny pieces, and break
    over-long sentences at commas, then at spaces.
    """
    chunks: list[str] = []
    for sentence in filter(None, (s.strip() for s in _SENTENCE_END.split(text.strip()))):
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        current = ""
        for part in _SOFT_BREAK.split(sentence):
            for word in part.split(" ") if len(part) > max_chars else [part]:
                candidate = f"{current} {word}".strip()
                if len(candidate) > max_chars and current:
                    chunks.append(current)
                    current = word
                else:
                    current = candidate
        if current:
            chunks.append(current)

    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) + len(chunk) + 1 <= max_chars and len(chunk) < 40:
            merged[-1] = f"{merged[-1]} {chunk}"
        else:
            merged.append(chunk)
    return merged


# Words a spoken phrase naturally starts with: breaking just before one sounds like a breath.
_BREAK_BEFORE = {
    # Hindi / Urdu (Devanagari)
    "कि", "और", "लेकिन", "क्योंकि", "तो", "इसलिए", "जो", "मगर", "पर", "ताकि", "जब", "अगर", "या", "बल्कि",
    # English (when the meeting language is English)
    "and", "but", "because", "so", "that", "which", "when", "if", "or",
}
_MIN_TAIL_CHARS = 12


def split_for_streaming(text: str, first_chars: int = 40) -> list[str]:
    """Split a translation into TTS requests so the listener hears something sooner.

    Indic-Speak returns a whole WAV per request and synthesises a little faster than real
    time (measured: ~0.065 s of work per character for ~0.08 s of Hindi audio). So:
    - the first chunk's length is the listener's wait: keep it short (~first_chars);
    - each later chunk is synthesised while earlier ones play, so it may be at most as long
      as everything before it, or the listener hears a gap mid-sentence.
    Breaks go at sentence ends, after commas, or before a conjunction, never inside a word.
    A natural break is taken once a chunk reaches 40% of its budget; without one, the first
    chunk is cut at 1.5x its budget and later chunks at their budget.
    """
    chunks: list[str] = []
    done_chars = 0  # characters already in earlier chunks (all sentences)
    for sentence in split_for_tts(text):
        sentence_start = len(chunks)
        current: list[str] = []
        for word in sentence.split():
            if chunks:
                budget = cap = max(first_chars, done_chars)
            else:
                budget, cap = first_chars, first_chars * 3 // 2
            size = len(" ".join(current))
            natural = bool(current) and (current[-1].endswith((",", ";", ":", "،"))
                                         or word.lower().strip(",.;:") in _BREAK_BEFORE)
            if current and ((natural and size >= budget * 2 // 5) or size + 1 + len(word) > cap):
                chunks.append(" ".join(current))
                done_chars += size
                current = []
            current.append(word)
        if current:
            tail = " ".join(current)
            if len(chunks) > sentence_start and len(tail) < _MIN_TAIL_CHARS:
                chunks[-1] = f"{chunks[-1]} {tail}"  # don't voice a lone word on its own
            else:
                chunks.append(tail)
            done_chars += len(tail)
    return chunks


_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?;:।॥)\]}%])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[{])\s+")


def tidy(text: str) -> str:
    """Remove the stray spaces Indic-Translate sometimes leaves around punctuation.

    "Got a chance to take blessings of Pathri Mata ." -> "... Pathri Mata."
    """
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text.strip())
    return _SPACE_AFTER_OPEN.sub(r"\1", text)
