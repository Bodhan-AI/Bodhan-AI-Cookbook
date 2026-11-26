"""Span utilities: sentence splitting with offsets, overlap, token diffing."""
from __future__ import annotations

import difflib

from .normalize import Token

_END = ".!?।॥"


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) offsets of sentences / lines. A '.' only ends a sentence when followed
    by whitespace or end of text, so decimals like 3.14 are safe."""
    spans: list[tuple[int, int]] = []
    start, n = 0, len(text)
    i = 0
    while i < n:
        c = text[i]
        boundary = False
        if c == "\n":
            boundary = True
            end = i
        elif c in _END:
            j = i
            while j + 1 < n and text[j + 1] in _END:
                j += 1
            if j + 1 >= n or text[j + 1].isspace():
                boundary = True
                end = j + 1
                i = j
        if boundary:
            _push(text, spans, start, end)
            start = i + 1
        i += 1
    _push(text, spans, start, n)
    return spans


def _push(text: str, spans: list, s: int, e: int) -> None:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    if e > s:
        spans.append((s, e))


def overlap(a: tuple[int, int] | list[int] | None, b: tuple[int, int] | list[int] | None) -> int:
    if not a or not b:
        return 0
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def spans_overlap(a, b) -> bool:
    return overlap(a, b) > 0


def token_span(tokens: list[Token], i: int, j: int) -> list[int] | None:
    """Char span covering tokens[i:j]."""
    if i >= j or not tokens:
        return None
    return [tokens[i].start, tokens[j - 1].end]


def diff_ops(a: list[str], b: list[str]):
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
