"""Span arithmetic and alignment helpers.

Spans are half-open character ranges ``[start, end)`` into the text as supplied.
A zero-width span ``[p, p]`` marks an insertion point (where omitted text
should have been); for overlap tests it is widened by ``tol`` characters.
"""

from __future__ import annotations

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .normalize import Token

Span = tuple[int, int]


def overlaps(a: Span | None, b: Span | None, tol: int = 2) -> bool:
    if a is None or b is None:
        return False
    a0, a1 = a
    b0, b1 = b
    if a1 - a0 == 0:
        a0, a1 = a0 - tol, a1 + tol
    if b1 - b0 == 0:
        b0, b1 = b0 - tol, b1 + tol
    return a0 < b1 and b0 < a1


def union(a: Span | None, b: Span | None) -> Span | None:
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), max(a[1], b[1]))


def narrowest(spans: list[Span | None]) -> Span | None:
    real = [s for s in spans if s is not None]
    if not real:
        return None
    return min(real, key=lambda s: (s[1] - s[0] if s[1] > s[0] else 10**9, s[0]))


def tokens_to_span(tokens: list[Token], i: int, j: int) -> Span | None:
    """Inclusive token index range [i, j] -> character span. None if out of range."""
    if not tokens or i < 0 or j < i or i >= len(tokens):
        return None
    j = min(j, len(tokens) - 1)
    return (tokens[i].start, tokens[j].end)


def span_to_token_range(tokens: list[Token], span: Span) -> tuple[int, int] | None:
    idx = [k for k, t in enumerate(tokens) if t.start < span[1] and span[0] < t.end]
    return (idx[0], idx[-1]) if idx else None


def word_alignment(ref: list[str], hyp: list[str]) -> list[tuple[str, int, int, int, int]]:
    """Levenshtein opcodes between two word lists: (tag, i1, i2, j1, j2), tag in equal/replace/delete/insert."""
    return [(op.tag, op.src_start, op.src_end, op.dest_start, op.dest_end) for op in Levenshtein.opcodes(ref, hyp)]


def char_error_rate(ref: str, hyp: str) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    return Levenshtein.distance(ref, hyp) / len(ref)


def similarity(a: str, b: str) -> float:
    """0..1 character similarity (normalised Indel ratio)."""
    if not a and not b:
        return 1.0
    return fuzz.ratio(a, b) / 100.0


def best_window_match(needle: str, haystack_tokens: list[str], max_extra: int = 1) -> tuple[float, int, int]:
    """Find the contiguous token window of haystack most similar to needle. Returns (score 0..100, i, j) inclusive."""
    best = (0.0, -1, -1)
    n = max(1, len(needle.split()))
    for i in range(len(haystack_tokens)):
        for w in range(max(1, n - 1), n + max_extra + 1):
            j = i + w - 1
            if j >= len(haystack_tokens):
                break
            score = fuzz.ratio(needle, " ".join(haystack_tokens[i : j + 1]))
            if score > best[0]:
                best = (score, i, j)
    return best
