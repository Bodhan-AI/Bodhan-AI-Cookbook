"""Text helpers: split long text for the models' limits, and clean text before speaking it."""

from __future__ import annotations

import re

# Sentence ends: . ? ! (English), । (danda), ॥ (double danda), and newlines.
_SENTENCE_END = re.compile(r"(?<=[.?!।॥])\s+|\n+")
_CLAUSE_BREAK = re.compile(r"(?<=[,;:،])\s+")


def split_text(text: str, max_chars: int) -> list[str]:
    """Split into pieces of at most `max_chars`, preferring sentence ends, then clauses,
    then spaces, and only as a last resort cutting a long word. Order is preserved and
    joining the pieces with a space gives back the same words."""
    text = text.strip()
    if not text:
        return []
    pieces: list[str] = []
    for sentence in (s.strip() for s in _SENTENCE_END.split(text)):
        if not sentence:
            continue
        pieces.extend(_split_long(sentence, max_chars))
    # Pack small sentences together up to max_chars.
    out: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > max_chars:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def _split_long(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    parts: list[str] = []
    for clause in _CLAUSE_BREAK.split(sentence):
        if len(clause) <= max_chars:
            parts.append(clause)
            continue
        words, cur = clause.split(), ""
        for w in words:
            while len(w) > max_chars:  # a single enormous word
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.append(w[:max_chars])
                w = w[max_chars:]
            if cur and len(cur) + 1 + len(w) > max_chars:
                parts.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            parts.append(cur)
    return parts


_TAGS = re.compile(r"<[^>]+>")
_MD_NOISE = re.compile(r"[#*_`>|~]+")
_SPACES = re.compile(r"[ \t]+")


def clean_for_speech(text: str) -> str:
    """Remove HTML tags and Markdown symbols so the voice does not read them out loud."""
    text = _TAGS.sub(" ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    text = _MD_NOISE.sub(" ", text)
    text = _SPACES.sub(" ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()