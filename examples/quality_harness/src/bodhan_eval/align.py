"""Back-translate a translation and line its sentences up with the source's (plan §8).

The alignment is the backbone for localization: a source sentence with no partner is an omission
candidate, a back-translated sentence with no partner is an addition candidate, and a pair that
matches poorly is a mistranslation candidate. This module only builds the alignment; deciding what
to flag is the back-translation check's job (§10).

Two ways to get the back-translation:
- align_item: the whole translation in one call. Exact source spans, one request per item. The
  back-translation may group sentences differently (Indic-Translate merges lines for Hindi); the
  aligner's 1-2 and 2-1 moves absorb that.
- align_item_by_segment: one call per translated sentence, so every pair also has an exact span in
  the translation. Several requests per item, so for items that are already suspicious.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from functools import lru_cache

from sacrebleu.metrics import CHRF

from .clients import Clients
from .normalize import normalize
from .segment import Segment, segments

# A 1-1 pair is kept when its similarity beats GAP; below that, two gaps (omission + addition) win.
# Unrelated English sentences score about 0.1-0.2 chrF, a faithful back-translation 0.5-0.9.
# A first-principles default: calibration (§14) replaces it.
GAP = 0.3
MERGE_PENALTY = 0.01  # prefer 1-1 over 1-2 / 2-1 when both explain the text equally well

# beta=1, not chrF's usual recall-weighted 2: a long back-translated sentence must not score well
# against a short source sentence just because it contains it.
_CHRF = CHRF(beta=1)


@lru_cache(maxsize=65_536)
def similarity(source: str, back: str) -> float:
    """chrF (0-1) of the back-translation against the source, on loosely normalized text."""
    a, b = normalize(source, loose=True).text, normalize(back, loose=True).text
    if not a or not b:
        return 0.0
    return _CHRF.sentence_score(b, [a]).score / 100


@dataclass(frozen=True)
class Pair:
    """One step of the alignment. Three shapes matter downstream:
    - back empty: source text with no counterpart (omission candidate)
    - src empty: back-translated text with no counterpart (addition candidate)
    - both present, score < GAP: a weak match (paraphrase or mistranslation, for the judge)
    """

    src: tuple[int, ...]  # indices into the source segments
    back: tuple[int, ...]  # indices into the back-translated segments
    score: float  # similarity of the joined texts; 0 when one side is empty


@dataclass
class Alignment:
    source: list[Segment]  # offsets into the item's source
    back: list[Segment]  # back-translated sentences (offsets into `back_text`)
    back_text: str
    pairs: list[Pair]
    target: list[Segment] | None = None  # by-segment mode only: target[k] is what back[k] translates


def align(src: list[str], back: list[str], gap: float = GAP) -> list[Pair]:
    """Monotone alignment by dynamic programming (Gale-Church style): moves 1-1, 1-k and k-1 for k <= 4,
    1-0 and 0-1.

    A matched move scores (similarity - gap) x the characters it covers, a skip scores 0, and the best
    total wins. Weighting by length means leaving a sentence unmatched costs what it would have
    contributed, so the aligner cannot drop text for free. Ties break the same way every time.

    Unmatched source and back-translated sentences that sit next to each other are then joined into
    one weak match: text that was reworded or mistranslated is not an omission plus an addition.
    """
    n, m = len(src), len(back)
    best = [[float("-inf")] * (m + 1) for _ in range(n + 1)]
    step: list[list[tuple[int, int, float] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    moves = ((1, 1), (1, 2), (2, 1), (1, 3), (3, 1), (1, 4), (4, 1), (1, 0), (0, 1))
    for i in range(n + 1):
        for j in range(m + 1):
            if best[i][j] == float("-inf"):
                continue
            for di, dj in moves:
                ni, nj = i + di, j + dj
                if ni > n or nj > m:
                    continue
                if di and dj:
                    a, b = " ".join(src[i:ni]), " ".join(back[j:nj])
                    sim = similarity(a, b)
                    value = (sim - gap - (MERGE_PENALTY if di + dj > 2 else 0.0)) * (len(a) + len(b))
                else:
                    sim, value = 0.0, 0.0
                if best[i][j] + value > best[ni][nj]:
                    best[ni][nj] = best[i][j] + value
                    step[ni][nj] = (di, dj, sim)
    pairs, i, j = [], n, m
    while i or j:
        di, dj, sim = step[i][j]  # type: ignore[misc]
        pairs.append(Pair(tuple(range(i - di, i)), tuple(range(j - dj, j)), round(sim, 4)))
        i, j = i - di, j - dj
    return _join_gaps(pairs[::-1], src, back)


def _join_gaps(pairs: list[Pair], src: list[str], back: list[str]) -> list[Pair]:
    """Merge each run of consecutive gaps into one pair; a run with both sides becomes a weak match."""
    out: list[Pair] = []
    run: list[Pair] = []
    for p in pairs + [Pair((), (), -1.0)]:  # sentinel flushes the last run
        if p.score != -1.0 and not (p.src and p.back):
            run.append(p)
            continue
        if run:
            s = tuple(k for r in run for k in r.src)
            b = tuple(k for r in run for k in r.back)
            score = similarity(" ".join(src[k] for k in s), " ".join(back[k] for k in b)) if s and b else 0.0
            out.append(Pair(s, b, round(score, 4)))
            run = []
        if p.score != -1.0:
            out.append(p)
    return out


def align_item(source: str, translation: str, clients: Clients, gap: float = GAP) -> Alignment:
    """One back-translation call for the whole item."""
    src = segments(source)
    back_text = clients.translate(translation, "en").text if segments(translation) else ""
    back = segments(back_text)
    return Alignment(src, back, back_text, align([s.text for s in src], [b.text for b in back], gap))


def align_item_by_segment(source: str, translation: str, clients: Clients, gap: float = GAP) -> Alignment:
    """One back-translation call per translated sentence, so each pair maps to an exact target span."""
    src, target = segments(source), segments(translation)
    texts = [clients.translate(t.text, "en").text.strip() for t in target]
    back_text = "\n".join(texts)
    back, pos = [], 0
    for t in texts:  # one back segment per target segment, even if the back-translation has several sentences
        back.append(Segment(pos, pos + len(t), t))
        pos += len(t) + 1
    pairs = align([s.text for s in src], texts, gap)
    return Alignment(src, back, back_text, pairs, target)


def project_span(al: Alignment, start: int, end: int) -> tuple[int, int] | None:
    """Source span -> the translation span it was rendered as (by-segment mode only).

    A source span whose sentences were all omitted maps to an insertion point after the previous
    translated sentence. None when the alignment has no target offsets.
    """
    if al.target is None:
        return None
    hit = [k for p in al.pairs if any(al.source[i].start < end and start < al.source[i].end for i in p.src) for k in p.back]
    if hit:
        return al.target[min(hit)].start, al.target[max(hit)].end
    before = [k for p in al.pairs for k in p.back if p.src and al.source[p.src[-1]].end <= start]
    pos = al.target[max(before)].end if before else (al.target[0].start if al.target else 0)
    return pos, pos


@dataclass(frozen=True)
class WordDiff:
    missing: list[tuple[int, int]]  # spans of the source text whose words are absent from the back-translation
    extra: list[str]  # back-translation words with no counterpart in the source
    # Each change: ("delete" | "replace" | "insert", source span or None, the back-translation's words there)
    changes: list[tuple[str, tuple[int, int] | None, str]]


def word_diff(source: str, back: str) -> WordDiff:
    """Word-level difference between a source sentence and its back-translation, as evidence."""
    ns, nb = normalize(source, loose=True), normalize(back, loose=True)
    a, b = _words(ns.text), _words(nb.text)
    ops = difflib.SequenceMatcher(None, [w for w, _, _ in a], [w for w, _, _ in b], autojunk=False).get_opcodes()
    missing, extra, changes = [], [], []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        span = ns.to_original(a[i1][1], a[i2 - 1][2]) if i2 > i1 else None
        words = " ".join(w for w, _, _ in b[j1:j2])
        if span:
            missing.append(span)
        if words:
            extra.append(words)
        changes.append((tag, span, words))
    return WordDiff(missing, extra, changes)


def _words(text: str) -> list[tuple[str, int, int]]:
    out, pos = [], 0
    for w in text.split(" "):
        if w:
            out.append((w, pos, pos + len(w)))
        pos += len(w) + 1
    return out
