"""MQM-style quality score: one number per item and per language that a model team can track across runs.

Each flag costs a penalty by severity (minor 1, major 5, critical 10, the usual MQM weights); the item scores
``max(0, 100 - 100 * penalty / source_words)``, so the same error costs more in a short text. A language's
score is the mean over its scored items. It summarises the flags; it is not a separate check.
"""

from __future__ import annotations

from .models import ItemResult

PENALTY = {"minor": 1, "major": 5, "critical": 10}


def item_score(r: ItemResult) -> float:
    words = max(1, len(r.item.source.split()))
    penalty = sum(PENALTY.get(f.severity, PENALTY["major"]) for f in r.flags)
    return round(max(0.0, 100.0 - 100.0 * penalty / words), 2)


def language_score(rs: list[ItemResult]) -> float | None:
    scored = [item_score(r) for r in rs if r.status == "ok"]
    return round(sum(scored) / len(scored), 2) if scored else None
