"""Word alignment between the text that should have been spoken (ref) and the ASR output (hyp).

A Levenshtein alignment over tokens, with two twists that matter for Indic ASR:
* fuzzy substitution: a substitution costs 1 - similarity, so a near-miss spelling counts less
  than a different word (ASR noise), and a split/merged compound pays roughly one edit;
* wildcard tokens: a reference token containing a digit or symbol (e.g. "100", "°C") may match
  0-6 hypothesis tokens at no cost, because TTS speaks "100" as one or more number words
  ("हंड्रेड", "एक सौ") and ASR writes them back as words.
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

WILDCARD_MAX = 6


@dataclass
class Op:
    kind: str  # "match" | "sub" | "del" | "ins" | "wild"
    ref: tuple[int, int]  # token range in ref [i0, i1)
    hyp: tuple[int, int]  # token range in hyp [j0, j1)
    cost: float = 0.0


def similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return Levenshtein.normalized_similarity(a, b)


def align(ref: list[str], hyp: list[str], wild: list[bool] | None = None) -> list[Op]:
    n, m = len(ref), len(hyp)
    wild = wild or [False] * n
    INF = float("inf")
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            d = D[i][j]
            if d == INF:
                continue
            if i < n and j < m and not wild[i]:
                sim = similarity(ref[i], hyp[j])
                c = d + (1.0 - sim)
                if c < D[i + 1][j + 1]:
                    D[i + 1][j + 1] = c
                    back[i + 1][j + 1] = ("match" if sim >= 0.999 else "sub", i, j)
            if i < n:
                c = d + (0.0 if wild[i] else 1.0)
                if c < D[i + 1][j]:
                    D[i + 1][j] = c
                    back[i + 1][j] = ("wild" if wild[i] else "del", i, j)
            if j < m:
                c = d + 1.0
                if c < D[i][j + 1]:
                    D[i][j + 1] = c
                    back[i][j + 1] = ("ins", i, j)
            if i < n and wild[i]:
                for k in range(1, min(WILDCARD_MAX, m - j) + 1):
                    if d < D[i + 1][j + k]:
                        D[i + 1][j + k] = d
                        back[i + 1][j + k] = ("wild", i, j)
    ops: list[Op] = []
    i, j = n, m
    while i > 0 or j > 0:
        kind, pi, pj = back[i][j]
        ops.append(Op(kind, (pi, i), (pj, j), D[i][j] - D[pi][pj]))
        i, j = pi, pj
    ops.reverse()
    return ops


def error_rate(ops: list[Op], n_ref: int) -> float:
    return sum(o.cost for o in ops) / max(n_ref, 1)


@dataclass
class Run:
    kind: str  # "del" | "ins"
    ref: tuple[int, int]
    hyp: tuple[int, int]
    length: int


def runs(ops: list[Op], kind: str, min_len: int = 1) -> list[Run]:
    """Maximal contiguous stretches of deletions (or insertions); substitutions with
    similarity < 0.3 inside a stretch are treated as part of it (a garbled word is not spoken)."""
    out: list[Run] = []
    cur: list[Op] = []

    def flush():
        if cur:
            length = sum((o.ref[1] - o.ref[0]) if kind == "del" else (o.hyp[1] - o.hyp[0]) for o in cur if o.kind == kind)
            if length >= min_len:
                out.append(Run(kind, (cur[0].ref[0], cur[-1].ref[1]), (cur[0].hyp[0], cur[-1].hyp[1]), length))
        cur.clear()

    for o in ops:
        if o.kind == kind or (cur and o.kind == "sub" and o.cost > 0.7):
            cur.append(o)
        else:
            flush()
    flush()
    return out
