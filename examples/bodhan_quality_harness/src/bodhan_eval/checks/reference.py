"""Reference-based scoring, dependency-free.

chrF (character n-gram F-score) is the right metric for Indic languages because
it is far less brittle to morphology than BLEU, and it needs no tokenizer. This
is a compact implementation so the harness has no extra dependency to install;
sacrebleu can be swapped in later without touching callers.
"""

from __future__ import annotations

from collections import Counter

from ..normalize import normalize
from .base import Flag, ItemContext


def _ngrams(text: str, n: int) -> Counter:
    text = normalize(text).replace(" ", "")
    return Counter(text[i:i + n] for i in range(max(0, len(text) - n + 1)))


def chrf(hypothesis: str, reference: str, max_n: int = 6, beta: float = 2.0) -> float:
    """chrF score in [0, 1]."""
    if not hypothesis or not reference:
        return 0.0
    precisions, recalls = [], []
    for n in range(1, max_n + 1):
        h, r = _ngrams(hypothesis, n), _ngrams(reference, n)
        if not h or not r:
            continue
        overlap = sum((h & r).values())
        precisions.append(overlap / sum(h.values()))
        recalls.append(overlap / sum(r.values()))
    if not precisions:
        return 0.0
    p = sum(precisions) / len(precisions)
    r = sum(recalls) / len(recalls)
    if p + r == 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * (p * r) / (b2 * p + r)


def check_reference(ctx: ItemContext, threshold: float = 0.45) -> list[Flag]:
    """Flag low similarity when a reference translation exists."""
    if not ctx.translation or not ctx.reference:
        return []
    score = chrf(ctx.translation, ctx.reference)
    if score >= threshold:
        return []
    severity = "critical" if score < 0.25 else "major"
    return [Flag(
        category="mistranslation", severity=severity,
        span=(0, min(len(ctx.translation), 60)),
        evidence=f"chrF vs reference = {score:.3f} (threshold {threshold})",
        check="reference.chrf", confidence=0.7,
    )]
