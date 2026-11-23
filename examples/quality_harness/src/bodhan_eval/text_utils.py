"""String-metric helpers. Only string-based metrics are allowed (no learned QE models).

- chrF++ via sacrebleu (imported lazily so the core can run without it installed).
- CER / WER implemented here with a classic edit-distance DP so we own the semantics and
  can also return the edit operations for localization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


def chrf(hypothesis: str, reference: str) -> float:
    """chrF++ score in [0, 100]. Higher is more similar."""
    from sacrebleu.metrics import CHRF

    metric = CHRF(word_order=2)  # chrF++ uses word bigrams
    return float(metric.sentence_score(hypothesis, [reference]).score)


@dataclass
class EditStats:
    distance: int
    length: int        # length of the reference sequence
    rate: float        # distance / length  (CER or WER depending on tokenization)
    ops: list[tuple[str, int, int]]  # (op, ref_index, hyp_index); op in {equal,replace,insert,delete}


def _levenshtein(ref: list, hyp: list) -> EditStats:
    n, m = len(ref), len(hyp)
    # DP table of distances
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    # backtrace for ops
    ops: list[tuple[str, int, int]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1):
            ops.append(("equal" if ref[i - 1] == hyp[j - 1] else "replace", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append(("delete", i - 1, j))
            i -= 1
        else:
            ops.append(("insert", i, j - 1))
            j -= 1
    ops.reverse()
    dist = dp[n][m]
    length = n if n > 0 else 1
    return EditStats(distance=dist, length=n, rate=dist / length, ops=ops)


def char_error_rate(reference: str, hypothesis: str) -> EditStats:
    return _levenshtein(list(reference), list(hypothesis))


def word_error_rate(reference: str, hypothesis: str) -> EditStats:
    return _levenshtein(reference.split(), hypothesis.split())


def token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of whitespace tokens (cheap, symmetric, no learned model)."""
    sa, sb = set(a.split()), set(b.split())
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


ScoreKind = Literal["chrf", "overlap"]
