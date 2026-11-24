"""Turn signals into flags.

Signals of the same category whose spans overlap form a *cluster* (one suspected error).
A cluster's score is a weighted noisy-OR:  score = 1 - prod(1 - w[check] * confidence)
so independent evidence accumulates, a single strong deterministic signal (digits, format,
script) can clear the bar alone, and a single noisy checker (back-translation, length) cannot.
A verifier denial scales the score down. A cluster becomes a flag if score >= threshold[category].

Weights and thresholds live in thresholds.json; calibration re-tunes the thresholds from labels.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from .checks.base import Signal
from .taxonomy import CATEGORIES, SEVERITY_RANK, area_of


def load_thresholds(path: Path | None = None) -> dict:
    text = Path(path).read_text(encoding="utf-8") if path else resources.files(__package__).joinpath("thresholds.json").read_text(encoding="utf-8")
    cfg = json.loads(text)
    for c in CATEGORIES:
        cfg["thresholds"].setdefault(c, cfg.get("default_threshold", 0.5))
    return cfg


def _overlap(a, b) -> bool:
    return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]


@dataclass
class Cluster:
    category: str
    signals: list[Signal] = field(default_factory=list)
    denial: float = 0.0  # verifier's confidence that this is NOT an error
    verified: bool | None = None
    verifier_reason: str = ""

    def score(self, weights: dict, deny_weight: float = 0.8) -> float:
        p = 1.0
        for s in self.signals:
            p *= 1.0 - min(1.0, weights.get(s.check, 0.5) * s.confidence)
        return (1.0 - p) * (1.0 - deny_weight * self.denial)

    def top(self, weights: dict, need: str = "span") -> Signal | None:
        cands = [s for s in self.signals if getattr(s, need) is not None]
        return max(cands, key=lambda s: weights.get(s.check, 0.5) * s.confidence, default=None)

    @property
    def span(self):
        return next((s.span for s in self.signals if s.span), None)

    @property
    def source_span(self):
        return next((s.source_span for s in self.signals if s.source_span), None)


def cluster_signals(signals: list[Signal]) -> list[Cluster]:
    clusters: list[Cluster] = []
    for s in sorted(signals, key=lambda s: (-s.confidence, s.check, s.span or (0, 0))):
        home = None
        for c in clusters:
            if c.category != s.category:
                continue
            if any(_overlap(s.span, t.span) or _overlap(s.source_span, t.source_span) for t in c.signals):
                home = c
                break
        if home is None and s.span is None and s.source_span is None:
            same = [c for c in clusters if c.category == s.category]
            home = same[0] if same else None
        if home is None and (s.span is not None or s.source_span is not None):
            # a located signal can join an unlocated cluster of the same category
            home = next((c for c in clusters if c.category == s.category and c.span is None and c.source_span is None), None)
        if home is None:
            home = Cluster(s.category)
            clusters.append(home)
        home.signals.append(s)
    return clusters


def to_flag(c: Cluster, cfg: dict) -> dict | None:
    w = cfg["weights"]
    score = c.score(w, cfg.get("deny_weight", 0.8))
    if score < cfg["thresholds"][c.category]:
        return None
    lead = c.top(w, "span") or c.top(w, "source_span") or max(c.signals, key=lambda s: w.get(s.check, 0.5) * s.confidence)
    judge_sev = next((s.severity for s in c.signals if s.check in ("judge", "verifier")), None)
    det_sev = max((s.severity for s in c.signals if s.check not in ("judge", "verifier", "length", "backtranslation")),
                  key=lambda x: SEVERITY_RANK[x], default=None)
    severity = det_sev or judge_sev or lead.severity
    src_lead = c.top(w, "source_span")
    return {
        "category": c.category,
        "area": area_of(c.category),
        "severity": severity,
        "span": list(lead.span) if lead.span else None,
        "source_span": list(src_lead.source_span) if src_lead else None,
        "confidence": round(score, 3),
        "message": lead.message,
        "sources": sorted({s.check for s in c.signals}),
        "verified": c.verified,
        "evidence": [{"check": s.check, "confidence": round(s.confidence, 3), "message": s.message, **({"detail": s.evidence} if s.evidence else {})}
                     for s in sorted(c.signals, key=lambda s: (-s.confidence, s.check))]
                    + ([{"check": "verifier", "denied": True, "confidence": round(c.denial, 3), "message": c.verifier_reason}] if c.denial else []),
    }


def flags_for(clusters: list[Cluster], cfg: dict) -> list[dict]:
    flags = [f for f in (to_flag(c, cfg) for c in clusters) if f]
    flags.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["category"], f["span"] or [10**9]))
    return flags
