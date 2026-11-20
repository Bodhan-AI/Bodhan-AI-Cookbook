"""Merge candidate flags from all checks into final flags.

Checks propose, this module decides:
  1. Group candidates of the same area (translation / speech) whose spans overlap
     substantially (IoU >= `merge_iou`), or overlap at all and share a category.
     Item-level flags (no span) group with other span-less flags of the same category.
  2. Category = the one with the highest reliability-weighted support in the group.
  3. Confidence = noisy-OR over the *checks* that agree on that category (each check counted
     once, at its highest confidence): independent evidence adds up, repeated evidence from
     one check does not.
  4. Span = the span of the strongest agreeing flag (most specific evidence wins).
  5. Number / term / mistranslation flags lying entirely inside an addition or
     wrong-language flag are folded into it (details.absorbed): they describe the same
     inserted or untranslated text, not separate errors.
  6. `apply_thresholds` keeps flags at or above the per-category threshold. Thresholds come
     from config / calibration; calibration sweeps them over the merged candidates.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bodhan_eval.categories import SEVERITIES, SEVERITY_WEIGHT, area_of
from bodhan_eval.checks.base import Flag
from bodhan_eval.params import Params

# How much a check's confidence is trusted relative to others when choosing the category.
# Generic priors (not dataset-tuned): rule-based checks with exact evidence rank highest.
DEFAULT_RELIABILITY = {
    "script": 1.0,
    "numbers": 1.0,
    "format": 0.95,
    "terms": 0.9,
    "audio": 0.9,
    "api": 1.0,
    "speech": 0.8,
    "judge": 0.8,
    "backtranslation": 0.7,
}


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else float(a == b)


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1] or a == b


def _linked(f: Flag, g: Flag, merge_iou: float) -> bool:
    if area_of(f.category) != area_of(g.category):
        return False
    if f.span is None or g.span is None:
        return f.span is None and g.span is None and f.category == g.category
    if f.category == g.category and _overlap(f.span, g.span):
        return True
    return _iou(f.span, g.span) >= merge_iou


def _groups(flags: list[Flag], merge_iou: float) -> list[list[Flag]]:
    parent = list(range(len(flags)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(flags)):
        for j in range(i + 1, len(flags)):
            if _linked(flags[i], flags[j], merge_iou):
                parent[find(i)] = find(j)
    out: dict[int, list[Flag]] = defaultdict(list)
    for i, f in enumerate(flags):
        out[find(i)].append(f)
    return list(out.values())


def combine(flags: list[Flag], params: Params) -> list[Flag]:
    """Merge candidates into one flag per distinct problem (no thresholding)."""
    p = params.section("combine")
    merge_iou = p.get("merge_iou", 0.5)
    rel = {**DEFAULT_RELIABILITY, **(p.get("reliability") or {})}

    merged: list[Flag] = []
    for group in _groups(flags, merge_iou):
        support: dict[str, float] = defaultdict(float)
        for f in group:
            support[f.category] += rel.get(f.check, 0.7) * f.confidence
        category = max(sorted(support), key=lambda c: support[c])
        agree = [f for f in group if f.category == category]

        best_per_check: dict[str, Flag] = {}
        for f in agree:
            if f.check not in best_per_check or f.confidence > best_per_check[f.check].confidence:
                best_per_check[f.check] = f
        miss = 1.0
        for f in best_per_check.values():
            miss *= 1.0 - max(0.0, min(1.0, f.confidence))
        confidence = round(1.0 - miss, 4)

        top = max(agree, key=lambda f: (rel.get(f.check, 0.7) * f.confidence, -_len(f.span)))
        severity = max((f.severity for f in agree), key=SEVERITIES.index)
        merged.append(
            Flag(
                category,
                severity,
                "combined",
                confidence,
                top.span,
                top.evidence,
                source_span=top.source_span
                or next((f.source_span for f in agree if f.source_span), None),
                time_span=top.time_span or next((f.time_span for f in agree if f.time_span), None),
                details={
                    "checks": sorted(best_per_check),
                    "evidence": [
                        {"check": f.check, "confidence": f.confidence, "evidence": f.evidence}
                        for f in sorted(best_per_check.values(), key=lambda f: -f.confidence)
                    ],
                    "disagreeing": [
                        {"check": f.check, "category": f.category, "confidence": f.confidence}
                        for f in group
                        if f.category != category
                    ],
                },
            )
        )
    merged = _absorb_contained(merged)
    merged.sort(key=lambda f: (area_of(f.category), f.span or (-1, -1), f.category))
    return merged


# A flag inside one of these spans is usually a consequence of it (the numbers and names of
# an inserted or untranslated sentence), not a separate error.
_CONTAINERS = ("addition", "wrong_language")
_ABSORBABLE = ("number", "term", "mistranslation")


def _absorb_contained(merged: list[Flag]) -> list[Flag]:
    containers = [f for f in merged if f.category in _CONTAINERS and f.span]
    out = []
    for f in merged:
        host = None
        if f.category in _ABSORBABLE and f.span:
            host = next(
                (c for c in containers
                 if c is not f and c.span[0] <= f.span[0] and f.span[1] <= c.span[1]),
                None,
            )  # fmt: skip
        if host is None:
            out.append(f)
        else:
            host.details.setdefault("absorbed", []).append(
                {"category": f.category, "confidence": f.confidence, "evidence": f.evidence}
            )
    return out


def _len(span: tuple[int, int] | None) -> int:
    return (span[1] - span[0]) if span else 10**9


def threshold_for(category: str, params: Params) -> float:
    p = params.section("combine")
    return float((p.get("thresholds") or {}).get(category, p.get("default_threshold", 0.5)))


def apply_thresholds(merged: list[Flag], params: Params) -> list[Flag]:
    return [f for f in merged if f.confidence >= threshold_for(f.category, params)]


def item_verdict(final: list[Flag]) -> dict[str, Any]:
    """clean / minor / major / critical, plus the MQM-style penalty for the item."""
    if not final:
        return {"verdict": "clean", "penalty": 0.0}
    worst = max((f.severity for f in final), key=SEVERITIES.index)
    return {
        "verdict": worst,
        "penalty": round(sum(SEVERITY_WEIGHT[f.severity] for f in final), 2),
    }
