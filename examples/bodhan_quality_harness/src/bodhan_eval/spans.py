"""Span handling.

Flags must point at the exact place in the *original* translation text so a
reviewer can highlight it. Normalization (NFKC, whitespace collapse) changes
indices, so span-finding runs on a length-preserving transform instead: digit
and punctuation substitution are 1:1, so every index stays valid.
"""

from __future__ import annotations

import re

from .normalize import _DIGITS, _PUNCT_MAP

_PUNCT_TABLE = str.maketrans(_PUNCT_MAP)


def span_preserving(text: str) -> str:
    """Digit/punctuation canonical form with identical length and indices."""
    return text.translate(_DIGITS).translate(_PUNCT_TABLE)


def find_all(pattern: re.Pattern[str], text: str) -> list[tuple[str, int, int]]:
    """Return (match, start, end) using a span-preserving view of ``text``."""
    view = span_preserving(text)
    return [(m.group(0), m.start(), m.end()) for m in pattern.finditer(view)]


def overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> float:
    """Intersection-over-union of two char spans, 0.0 when either is missing."""
    if not a or not b:
        return 0.0
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if hi <= lo:
        return 0.0
    inter = hi - lo
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


def overlaps(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    """True when two spans intersect at all (the spec's localization rule)."""
    if not a or not b:
        return False
    return min(a[1], b[1]) > max(a[0], b[0])


def snippet(text: str, span: tuple[int, int] | None, pad: int = 24) -> str:
    """Human-readable evidence around a span."""
    if not span:
        return ""
    lo = max(0, span[0] - pad)
    hi = min(len(text), span[1] + pad)
    out = text[lo:hi].replace("\n", " ")
    return ("..." if lo > 0 else "") + out + ("..." if hi < len(text) else "")
