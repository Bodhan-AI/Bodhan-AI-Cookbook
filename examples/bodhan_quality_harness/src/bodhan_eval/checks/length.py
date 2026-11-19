"""Length-ratio checks for omission and addition.

A coarse but cheap net for content that appears or disappears wholesale. The
thresholds are deliberately configurable and are the ones the calibration step
sweeps — they must be tuned from labelled data, never guessed.
"""

from __future__ import annotations

import re

from ..normalize import word_count
from ..spans import span_preserving
from .base import Flag, ItemContext

# Tuned by calibration on labelled data; see calibrate.py.
DEFAULT_LO = 0.55   # below this ratio -> likely omission
DEFAULT_HI = 2.20   # above this ratio -> likely addition


def check_length(ctx: ItemContext, lo: float = DEFAULT_LO, hi: float = DEFAULT_HI) -> list[Flag]:
    if not ctx.translation:
        return []
    s_words = word_count(ctx.source)
    t_words = word_count(ctx.translation)
    if s_words < 8:
        return []  # a ratio on fewer than 8 words is noise

    ratio = t_words / s_words
    span = (0, min(len(ctx.translation), 60))

    if ratio < lo:
        severity = "critical" if ratio < lo * 0.6 else "major"
        return [Flag(
            category="omission", severity=severity, span=span,
            evidence=f"translation is {ratio:.2f}x the source length "
                     f"({t_words} vs {s_words} words) — content likely dropped",
            check="length.omission", confidence=0.55,
        )]
    if ratio > hi:
        return [Flag(
            category="addition", severity="major", span=span,
            evidence=f"translation is {ratio:.2f}x the source length "
                     f"({t_words} vs {s_words} words) — content likely added",
            check="length.addition", confidence=0.5,
        )]
    return []


SHINGLE = 3

# Markdown table separators, table rows and fenced code blocks.
STRUCTURAL_LINES = re.compile(
    r"(?m)^\s*\|?[\s:\-|]{3,}\|?\s*$|```.*?```", re.DOTALL)


def check_repeats(ctx: ItemContext) -> list[Flag]:
    """Repeated word sequences inside the translation.

    A model that loops duplicates a phrase verbatim, which is content that is
    not in the source. Much sharper than a length ratio, and cheap. The span is
    taken from the second occurrence so the flag localizes exactly.
    """
    if not ctx.translation:
        return []
    # Table separator rows ("| --- |") and fenced code repeat legitimately.
    # Strip them first, or every Markdown table becomes a false alarm.
    cleaned = STRUCTURAL_LINES.sub("", ctx.translation)
    view = span_preserving(cleaned)
    words = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", view)]
    if len(words) < SHINGLE * 2:
        return []

    seen: dict[tuple, int] = {}
    for i in range(len(words) - SHINGLE + 1):
        sh = tuple(w[0] for w in words[i:i + SHINGLE])
        if sh in seen:
            first = seen[sh]
            span = (words[i][1], words[i + SHINGLE - 1][2])
            phrase = " ".join(sh)
            return [Flag(
                category="addition", severity="major", span=span,
                evidence=f"repeated {SHINGLE}-word sequence: '{phrase[:70]}' "
                         f"(first at word {first}, again at word {i})",
                check="length.repeat", confidence=0.75,
            )]
        seen[sh] = i
    return []
