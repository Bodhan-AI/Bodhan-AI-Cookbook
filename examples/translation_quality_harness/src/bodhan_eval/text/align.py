"""Edit-distance alignment with backtrace, at character and word level."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


def levenshtein(a: Sequence, b: Sequence) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


def error_rate(ref: Sequence, hyp: Sequence) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


@dataclass(frozen=True)
class Op:
    kind: str  # "eq" | "sub" | "del" | "ins"
    i: int | None  # reference index
    j: int | None  # hypothesis index
    sim: float = 0.0


def align_words(ref: list[str], hyp: list[str], wildcard: Callable[[str], bool] | None = None) -> list[Op]:
    """Weighted Levenshtein: substituting similar words is cheaper, so ASR near-misses align as substitutions.

    `wildcard(ref_word)` marks reference tokens (digits, Latin words) whose spoken form legitimately differs.
    """
    n, m = len(ref), len(hyp)
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[str]] = [[""] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(1, n + 1):
        cost[i][0], back[i][0] = float(i), "del"
    for j in range(1, m + 1):
        cost[0][j], back[0][j] = float(j), "ins"
    sims: dict[tuple[int, int], float] = {}
    for i in range(1, n + 1):
        r = ref[i - 1]
        is_wild = bool(wildcard and wildcard(r))
        for j in range(1, m + 1):
            h = hyp[j - 1]
            if r == h:
                sub, sim = 0.0, 1.0
            elif is_wild:
                sub, sim = 0.0, 1.0
            else:
                sim = similarity(r, h)
                sub = 0.5 + 0.5 * (1.0 - sim)
            sims[(i, j)] = sim
            options = ((cost[i - 1][j - 1] + sub, "diag"), (cost[i - 1][j] + 1.0, "del"), (cost[i][j - 1] + 1.0, "ins"))
            best = min(options, key=lambda x: x[0])
            cost[i][j], back[i][j] = best
    ops: list[Op] = []
    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j]
        if move == "diag":
            r, h = ref[i - 1], hyp[j - 1]
            sim = sims.get((i, j), 0.0)
            kind = "eq" if r == h or (wildcard and wildcard(r)) else "sub"
            ops.append(Op(kind, i - 1, j - 1, sim))
            i, j = i - 1, j - 1
        elif move == "del":
            ops.append(Op("del", i - 1, None))
            i -= 1
        else:
            ops.append(Op("ins", None, j - 1))
            j -= 1
    ops.reverse()
    return ops


def runs(ops: list[Op], kinds: set[str]) -> list[list[Op]]:
    """Maximal consecutive runs of ops whose kind is in `kinds`."""
    out: list[list[Op]] = []
    current: list[Op] = []
    for op in ops:
        if op.kind in kinds:
            current.append(op)
        else:
            if current:
                out.append(current)
            current = []
    if current:
        out.append(current)
    return out
