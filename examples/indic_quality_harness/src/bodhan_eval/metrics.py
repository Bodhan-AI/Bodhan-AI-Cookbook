"""Dependency-free text similarity metrics."""

from __future__ import annotations

from collections import Counter

from bodhan_eval.normalize import normalize_text


def _char_ngrams(text: str, n: int) -> Counter:
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def chrf(
    hypothesis: str,
    reference: str,
    max_order: int = 6,
    beta: float = 2.0,
) -> float:
    """Character n-gram F-score (chrF, Popović 2015), in the range 0..1.

    Whitespace is removed before n-gram extraction (as in sacreBLEU), digits
    are normalised to ASCII, and precision/recall are averaged over the
    n-gram orders that exist in both strings. chrF is script-agnostic, which
    makes it a reasonable default for Indic languages where word-level
    metrics are distorted by agglutination.
    """
    hyp = normalize_text(hypothesis).replace(" ", "")
    ref = normalize_text(reference).replace(" ", "")

    if not hyp and not ref:
        return 1.0
    if not hyp or not ref:
        return 0.0

    precisions = []
    recalls = []

    for n in range(1, max_order + 1):
        hyp_ngrams = _char_ngrams(hyp, n)
        ref_ngrams = _char_ngrams(ref, n)

        if not hyp_ngrams or not ref_ngrams:
            continue

        overlap = sum((hyp_ngrams & ref_ngrams).values())
        precisions.append(overlap / sum(hyp_ngrams.values()))
        recalls.append(overlap / sum(ref_ngrams.values()))

    if not precisions:
        return 0.0

    precision = sum(precisions) / len(precisions)
    recall = sum(recalls) / len(recalls)

    if precision + recall == 0:
        return 0.0

    beta_sq = beta ** 2
    return round(
        (1 + beta_sq) * precision * recall / (beta_sq * precision + recall),
        4,
    )
