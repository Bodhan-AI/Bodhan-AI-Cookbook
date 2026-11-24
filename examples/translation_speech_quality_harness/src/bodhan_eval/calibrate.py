"""Calibrate per-category confidence thresholds on labelled items.

Labels come from the dataset itself (injected errors, or human labels) and from an optional
`--calibration` JSONL ({"id", "labels"} per line, treated as complete human labels).

Label scope decides what an unmatched flag means:
  complete       (human labels, or a labelled item with no scope): every unmatched flag is
                 a false positive.
  assumed_clean  (untouched controls from the injector): same, but the true error rate of
                 the system output is unknown, so precision here is a lower bound.
  injected       only the planted error is labelled: unmatched flags are NOT counted as
                 false positives (the untouched rest of the text may contain real errors).

For each category the threshold with the best F1 is chosen, subject to at most
`max_clean_flag_rate` (default 10%) of clean items carrying any flag; categories with fewer
than `min_labels` labels keep their default threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bodhan_eval.categories import CATEGORIES
from bodhan_eval.checks.base import Flag
from bodhan_eval.combine import threshold_for
from bodhan_eval.params import Params


@dataclass
class LabelRec:
    category: str
    span: tuple[int, int] | None
    approx: bool = False


@dataclass
class CalItem:
    id: str
    scope: str  # complete | assumed_clean | injected
    labels: list[LabelRec]
    candidates: list[Flag]

    @property
    def clean(self) -> bool:
        return self.scope in ("complete", "assumed_clean") and not self.labels


def span_match(label: LabelRec, flag: Flag, tol: int) -> bool:
    if flag.category != label.category:
        return False
    ls, fs = label.span, flag.span
    if ls is None or fs is None:
        return True
    a, b = ls
    if a == b:  # zero-width label (deletion point)
        return fs[0] - tol <= a <= fs[1] + tol
    return fs[0] < b + tol and a - tol < fs[1]


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 1.0


def match_item(it: CalItem, threshold: dict[str, float]) -> tuple[list, list[Flag], list[LabelRec]]:
    """Greedy one-to-one matching of flags (at or above their threshold) to labels.
    Returns (matched (label, flag) pairs, unmatched flags, unmatched labels)."""
    flags = sorted(
        (f for f in it.candidates if f.confidence >= threshold.get(f.category, 0.5)),
        key=lambda f: -f.confidence,
    )
    free = list(it.labels)
    pairs, extra = [], []
    for f in flags:
        lab = next((lb for lb in free if span_match(lb, f, 40 if lb.approx else 3)), None)
        if lab is None:
            extra.append(f)
        else:
            free.remove(lab)
            pairs.append((lab, f))
    return pairs, extra, free


def evaluate(items: list[CalItem], threshold: dict[str, float]) -> dict[str, Any]:
    per: dict[str, dict[str, float]] = {c: {"tp": 0, "fp": 0, "fn": 0} for c in CATEGORIES}
    clean_flagged = clean_total = 0
    ious = []
    located = with_span = 0
    for it in items:
        pairs, extra, missed = match_item(it, threshold)
        for lab, f in pairs:
            per[lab.category]["tp"] += 1
            if lab.span and f.span and lab.span[0] != lab.span[1] and not lab.approx:
                ious.append(_iou(lab.span, f.span))
            if lab.span and f.span:
                with_span += 1
                located += _overlaps(lab.span, f.span)
        for lab in missed:
            per[lab.category]["fn"] += 1
        if it.scope != "injected":
            for f in extra:
                per[f.category]["fp"] += 1
        if it.clean:
            clean_total += 1
            clean_flagged += bool(extra)
    out: dict[str, Any] = {}
    for c, v in per.items():
        tp, fp, fn = v["tp"], v["fp"], v["fn"]
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * r / (p + r) if p and r else (0.0 if (tp + fp + fn) else None)
        out[c] = {"tp": tp, "fp": fp, "fn": fn, "labels": tp + fn, "precision": p,
                  "recall": r, "f1": f1}  # fmt: skip
    return {
        "per_category": out,
        "clean_items": clean_total,
        "clean_flag_rate": clean_flagged / clean_total if clean_total else None,
        "span_iou_mean": round(sum(ious) / len(ious), 4) if ious else None,
        # share of detections whose span really overlaps the label (no tolerance);
        # a zero-width label (deletion point) counts when it lies inside the flagged span
        "span_overlap_rate": round(located / with_span, 4) if with_span else None,
    }


def _overlaps(ls: tuple[int, int], fs: tuple[int, int]) -> bool:
    if ls[0] == ls[1]:
        return fs[0] <= ls[0] <= fs[1]
    return min(ls[1], fs[1]) > max(ls[0], fs[0])


def category_accuracy(items: list[CalItem]) -> float | None:
    """Of the labels that some candidate overlaps (any category), the share where the
    best-overlapping candidate has the right category."""
    right = total = 0
    for it in items:
        for lab in it.labels:
            hits = [f for f in it.candidates if _near(f.span, lab.span)]
            if not hits:
                continue
            total += 1
            right += max(hits, key=lambda f: f.confidence).category == lab.category
    return round(right / total, 4) if total else None


def _near(fs: tuple[int, int] | None, ls: tuple[int, int] | None, tol: int = 3) -> bool:
    return bool(fs and ls) and fs[0] <= ls[1] + tol and ls[0] - tol <= fs[1]


def calibrate(items: list[CalItem], params: Params) -> dict[str, Any]:
    p = params.section("calibration")
    max_clean = p.get("max_clean_flag_rate", 0.10)
    min_labels = p.get("min_labels", 3)
    defaults = {c: threshold_for(c, params) for c in CATEGORIES}
    chosen = dict(defaults)
    notes: dict[str, str] = {}

    n_labels = {c: sum(lb.category == c for it in items for lb in it.labels) for c in CATEGORIES}
    grid = sorted({round(x / 20, 2) for x in range(1, 20)}
                  | {round(f.confidence, 4) for it in items for f in it.candidates})  # fmt: skip

    for c in CATEGORIES:
        if n_labels[c] < min_labels:
            notes[c] = f"kept default: {n_labels[c]} label(s) < {min_labels}"
            continue
        best = None
        for t in grid:
            trial = {**chosen, c: t}
            ev = evaluate(items, trial)
            f1 = ev["per_category"][c]["f1"] or 0.0
            ok = ev["clean_flag_rate"] is None or ev["clean_flag_rate"] <= max_clean
            # Feasible first, then F1; among ties, the threshold closest to the default (with
            # few labels many thresholds tie, and the extremes just memorise this data).
            key = (ok, round(f1, 6), -abs(t - defaults[c]), t)
            if best is None or key > best[0]:
                best = (key, t)
        chosen[c] = best[1]
        notes[c] = "calibrated" if best[0][0] else "calibrated (clean-rate budget not met)"

    # Global guard: if the combined clean-item flag rate is still too high, raise the
    # threshold of the category flagging the most clean items until it fits.
    for _ in range(200):
        ev = evaluate(items, chosen)
        if ev["clean_flag_rate"] is None or ev["clean_flag_rate"] <= max_clean:
            break
        fp_by_cat = {c: v["fp"] for c, v in ev["per_category"].items() if v["fp"]}
        if not fp_by_cat:
            break
        worst = max(fp_by_cat, key=fp_by_cat.get)
        chosen[worst] = min(1.0, round(chosen[worst] + 0.05, 4))
        notes[worst] = "raised to meet the clean-item flag budget"

    final = evaluate(items, chosen)
    return {
        "thresholds": chosen,
        "defaults": defaults,
        "notes": notes,
        "labels": n_labels,
        "items": {"total": len(items), "by_scope": _count(i.scope for i in items)},
        "metrics": final,
        "category_accuracy": category_accuracy(items),
        "max_clean_flag_rate": max_clean,
        # Thresholds were chosen on these same items; use --thresholds on held-out data
        # for unbiased numbers.
        "in_sample": True,
    }


def _count(xs) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def thresholds_from_file(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Params override from a calibration.json (for --thresholds)."""
    return {"combine": {"thresholds": dict(data["thresholds"])}}
