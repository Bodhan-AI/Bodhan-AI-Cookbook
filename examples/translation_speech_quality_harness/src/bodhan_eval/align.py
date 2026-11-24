"""Token alignment between a reference sequence (what should be there) and a hypothesis
(what we observed: an ASR transcript, a back-translation, ...).

Returns explicit edit operations with token indices, so callers can turn runs of
deletions/insertions into character spans on the original text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

from bodhan_eval.normalize import Token


@dataclass(frozen=True)
class Op:
    kind: str  # "equal" | "replace" | "delete" | "insert"
    ref_start: int
    ref_end: int  # exclusive
    hyp_start: int
    hyp_end: int  # exclusive

    @property
    def ref_len(self) -> int:
        return self.ref_end - self.ref_start

    @property
    def hyp_len(self) -> int:
        return self.hyp_end - self.hyp_start


def align(ref: list[str], hyp: list[str]) -> list[Op]:
    """Levenshtein alignment over token lists (deterministic tie-breaking by rapidfuzz)."""
    ops = Levenshtein.opcodes(ref, hyp)
    return [Op(o.tag, o.src_start, o.src_end, o.dest_start, o.dest_end) for o in ops]


def align_tokens(ref: list[Token], hyp: list[Token]) -> list[Op]:
    return align([t.norm for t in ref], [t.norm for t in hyp])


def error_rate(ref: list[str], hyp: list[str]) -> float:
    """Edit distance / len(ref). 0.0 for identical, can exceed 1.0 with many insertions."""
    if not ref:
        return 0.0 if not hyp else 1.0
    return Levenshtein.distance(ref, hyp) / len(ref)


def char_error_rate(ref: str, hyp: str) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    return Levenshtein.distance(ref, hyp) / len(ref)


def counts(ops: list[Op]) -> dict[str, int]:
    """Token-level substitution / deletion / insertion / match counts."""
    c = {"equal": 0, "sub": 0, "del": 0, "ins": 0}
    for o in ops:
        if o.kind == "equal":
            c["equal"] += o.ref_len
        elif o.kind == "replace":
            common = min(o.ref_len, o.hyp_len)
            c["sub"] += common
            c["del"] += o.ref_len - common
            c["ins"] += o.hyp_len - common
        elif o.kind == "delete":
            c["del"] += o.ref_len
        else:
            c["ins"] += o.hyp_len
    return c


def missing_runs(ops: list[Op], *, include_replace: bool = True) -> list[tuple[int, int]]:
    """Contiguous ref-token ranges [start, end) with no matching hyp token.

    Adjacent delete/replace ops are merged: an ASR that garbles one word next to a dropped
    clause should still read as one missing stretch, not two.
    """
    runs: list[tuple[int, int]] = []
    for o in ops:
        if o.kind == "delete" or (include_replace and o.kind == "replace"):
            if runs and runs[-1][1] == o.ref_start:
                runs[-1] = (runs[-1][0], o.ref_end)
            else:
                runs.append((o.ref_start, o.ref_end))
    return runs


def extra_runs(ops: list[Op], *, include_replace: bool = False) -> list[tuple[int, int]]:
    """Contiguous hyp-token ranges [start, end) with no counterpart in ref."""
    runs: list[tuple[int, int]] = []
    for o in ops:
        if o.kind == "insert" or (include_replace and o.kind == "replace"):
            if runs and runs[-1][1] == o.hyp_start:
                runs[-1] = (runs[-1][0], o.hyp_end)
            else:
                runs.append((o.hyp_start, o.hyp_end))
    return runs


def tokens_to_span(tokens: list[Token], start: int, end: int) -> tuple[int, int] | None:
    """Character span covering tokens[start:end] in the original text."""
    if start >= end or not tokens:
        return None
    return (tokens[start].start, tokens[end - 1].end)


def repeated_ngrams(
    seq: list[str], *, min_n: int = 2, max_n: int = 12
) -> list[tuple[int, int, int]]:
    """Immediately repeated n-grams: returns (start, n, times) where seq[start:start+n] is
    followed by at least one identical copy. Longest n wins; non-overlapping, left to right."""
    found: list[tuple[int, int, int]] = []
    i = 0
    L = len(seq)
    while i < L:
        best = None
        for n in range(min(max_n, (L - i) // 2), min_n - 1, -1):
            gram = seq[i : i + n]
            times = 1
            while seq[i + times * n : i + (times + 1) * n] == gram:
                times += 1
            if times > 1:
                best = (i, n, times)
                break
        if best:
            found.append(best)
            i += best[1] * best[2]
        else:
            i += 1
    return found


# ---------------------------------------------------------------------------
# Sentence / segment alignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegPair:
    """One step of a monotonic segment alignment. `a` or `b` is empty for a gap."""

    a: tuple[int, ...]
    b: tuple[int, ...]
    score: float  # similarity of the joined segments (0..100); 0.0 for gaps


def align_segments(
    a: list[str],
    b: list[str],
    sim: Callable[[str, str], float],
    *,
    min_sim: float,
    merge_penalty: float = 2.0,
) -> list[SegPair]:
    """Monotonic alignment of two segment lists maximising total similarity.

    Moves: 1-1, 2-1 and 1-2 matches (a sentence split or merged by the system), or a gap
    on either side. A match is only allowed when its similarity reaches `min_sim`; it scores
    `sim - min_sim` (minus `merge_penalty` for 2-1 / 1-2), a gap scores 0. So segments are
    left unmatched exactly when nothing similar enough exists in order.
    """
    n, m = len(a), len(b)
    neg = float("-inf")
    best = [[neg] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, float] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    memo: dict[tuple[int, int, int, int], float] = {}

    def s(i: int, di: int, j: int, dj: int) -> float:
        k = (i, di, j, dj)
        if k not in memo:
            memo[k] = sim(" ".join(a[i : i + di]), " ".join(b[j : j + dj]))
        return memo[k]

    moves = ((1, 1), (2, 1), (1, 2), (1, 0), (0, 1))  # order = deterministic tie-break
    for i in range(n + 1):
        for j in range(m + 1):
            cur = best[i][j]
            if cur == neg:
                continue
            for di, dj in moves:
                ni, nj = i + di, j + dj
                if ni > n or nj > m:
                    continue
                if di and dj:
                    score = s(i, di, j, dj)
                    if score < min_sim:
                        continue
                    gain = score - min_sim - (merge_penalty if di + dj > 2 else 0.0)
                else:
                    score, gain = 0.0, 0.0
                if cur + gain > best[ni][nj]:
                    best[ni][nj] = cur + gain
                    back[ni][nj] = (di, dj, score)

    out: list[SegPair] = []
    i, j = n, m
    while i or j:
        step = back[i][j]
        assert step is not None
        di, dj, score = step
        out.append(SegPair(tuple(range(i - di, i)), tuple(range(j - dj, j)), score))
        i, j = i - di, j - dj
    out.reverse()
    return out
