"""Turn signals into flags.

1. Cluster signals that are about the same problem: same area (translation or
   speech), compatible categories, and overlapping location (translation span or
   source span). Located signals are clustered first; then each signal with no
   location ("something is missing somewhere") joins the *strongest* cluster of
   its own category, so it corroborates the most likely problem instead of
   lifting a weak candidate over the threshold or becoming a flag of its own.
2. Combine evidence with a noisy-OR over the strongest signal of each check
   (each judge replica counts as its own source):  P = 1 - prod(1 - s_i).
   Two independent weak signals (judge + back-translation, or two judge replicas)
   therefore beat one; one noisy checker alone usually does not.
3. The cluster's category is the one with most total evidence (ties broken by
   ``precedence`` in taxonomy.json); its span is the narrowest span offered.
4. Score >= threshold[category] -> flag; within ``uncertain_margin`` below it ->
   review queue; else dropped (still logged).
"""

from __future__ import annotations

from .config import category_area, category_group, taxonomy
from .models import Flag, Item, Signal
from .spans import narrowest, overlaps

COMPATIBLE = {
    frozenset(p)
    for p in [
        ("omission", "number"),
        ("addition", "number"),
        ("mistranslation", "term"),
        ("omission", "mistranslation"),
        ("speech_omission", "speech_garbled"),
        ("speech_repetition", "speech_garbled"),
    ]
}
SEV_RANK = {"minor": 0, "major": 1, "critical": 2}


def _compatible(a: str, b: str) -> bool:
    return a == b or category_group(a) == category_group(b) or frozenset((a, b)) in COMPATIBLE


def _located(s: Signal) -> bool:
    return (s.span is not None and s.span[1] > s.span[0]) or s.source_span is not None


class Cluster:
    def __init__(self, s: Signal):
        self.members = [s]

    def accepts(self, s: Signal) -> bool:
        """A located signal joins when area and category are compatible and the locations overlap."""
        if category_area(s.category) != category_area(self.members[0].category):
            return False
        if not any(_compatible(s.category, m.category) for m in self.members):
            return False
        return any(overlaps(s.span, m.span) or overlaps(s.source_span, m.source_span) for m in self.members)

    def has_category(self, cat: str) -> bool:
        return any(m.category == cat for m in self.members)

    def score(self) -> float:
        best: dict[tuple, float] = {}
        for m in self.members:
            k = (m.check, m.details.get("replica"))
            best[k] = max(best.get(k, 0.0), m.score)
        p = 1.0
        for v in best.values():
            p *= 1.0 - v
        return 1.0 - p

    def category(self) -> str:
        prec = taxonomy()["precedence"]
        totals: dict[str, float] = {}
        for m in self.members:
            totals[m.category] = totals.get(m.category, 0.0) + m.score
        return max(totals, key=lambda c: (round(totals[c], 6), -prec.index(c) if c in prec else -99))


def _signal_order(s: Signal) -> tuple:
    return (-s.score, s.check, s.category, s.span or (-1, -1))


def cluster_signals(signals: list[Signal]) -> list[Cluster]:
    clusters: list[Cluster] = []
    for s in sorted((s for s in signals if _located(s)), key=_signal_order):
        target = next((c for c in clusters if c.accepts(s)), None)
        if target is None:
            clusters.append(Cluster(s))
        else:
            target.members.append(s)
    for s in sorted((s for s in signals if not _located(s)), key=_signal_order):
        same = [c for c in clusters if c.has_category(s.category)]
        if same:  # strongest first; ties -> the earliest cluster, so the result is deterministic
            max(same, key=lambda c: (round(c.score(), 9), -clusters.index(c))).members.append(s)
        else:
            clusters.append(Cluster(s))
    return clusters


def to_flag(c: Cluster, item: Item) -> Flag:
    cat = c.category()
    mine = [m for m in c.members if m.category == cat] or c.members
    sev = max((m.severity for m in mine), key=lambda s: SEV_RANK.get(s, 1))
    span = narrowest([m.span for m in mine]) or narrowest([m.span for m in c.members])
    sspan = narrowest([m.source_span for m in mine]) or narrowest([m.source_span for m in c.members])
    tr = item.translation or ""
    text = tr[span[0] : span[1]] if span and span[1] > span[0] else None
    stext = item.source[sspan[0] : sspan[1]] if sspan else None
    evidence = [
        {"check": m.check, "category": m.category, "score": round(m.score, 4), "detail": m.evidence}
        for m in sorted(c.members, key=lambda m: -m.score)
    ]
    checks = sorted({m.check for m in c.members})
    return Flag(
        category=cat,
        severity=sev,
        confidence=c.score(),
        span=span,
        source_span=sspan,
        text=text,
        source_text=stext,
        evidence=evidence,
        checks=checks,
    )


def fuse(
    item: Item, signals: list[Signal], thresholds: dict[str, float], margin: float
) -> tuple[list[Flag], list[Flag], list[Flag]]:
    """Return (flags, uncertain, below_threshold)."""
    flags, uncertain, below = [], [], []
    for c in cluster_signals(signals):
        f = to_flag(c, item)
        t = thresholds.get(f.category, 0.5)
        if f.confidence >= t:
            flags.append(f)
        elif f.confidence >= t - margin:
            uncertain.append(f)
        else:
            below.append(f)
    key = lambda f: (f.span or f.source_span or (10**9, 0), f.category)
    return sorted(flags, key=key), sorted(uncertain, key=key), sorted(below, key=key)
