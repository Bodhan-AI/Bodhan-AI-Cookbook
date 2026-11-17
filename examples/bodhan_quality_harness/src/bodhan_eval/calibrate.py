"""Calibration and scoring against human labels.

This is what turns the harness from a pile of heuristics into a measured system:
per-category precision and recall, localization and category accuracy, and
thresholds swept from labelled data rather than guessed.

Matching rule: a prediction matches a label when the categories agree and their
spans overlap (or the label has no span, in which case category alone decides).
"""

from __future__ import annotations

from collections import defaultdict

from .checks.base import Flag, ItemContext
from .spans import overlaps

# Categories a prediction may use to explain a given seeded label type.
COMPATIBLE = {
    "number": {"number"},
    "term": {"term"},
    "format": {"format"},
    "language": {"language"},
    "omission": {"omission", "mistranslation"},
    "addition": {"addition", "mistranslation"},
    "mistranslation": {"mistranslation", "omission", "addition"},
    "speech_omission": {"speech_omission"},
    "speech_repetition": {"speech_repetition"},
    "speech_garbled": {"speech_garbled"},
}


def _label_span(label: dict) -> tuple[int, int] | None:
    sp = label.get("span")
    if isinstance(sp, (list, tuple)) and len(sp) == 2:
        try:
            return (int(sp[0]), int(sp[1]))
        except (TypeError, ValueError):
            return None
    return None


def score(predictions: dict[str, list[Flag]], items: list[ItemContext]) -> dict:
    """Precision, recall, localization and category accuracy."""
    by_cat = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "localized": 0, "cat_ok": 0})
    clean_items = [it for it in items if not it.labels]
    flagged_clean = sum(1 for it in clean_items if predictions.get(it.id))

    for it in items:
        preds = predictions.get(it.id, [])
        labels = it.labels or []
        used: set[int] = set()

        for lab in labels:
            ltype = str(lab.get("type", "")).strip().lower()
            lspan = _label_span(lab)
            ok_cats = COMPATIBLE.get(ltype, {ltype})
            hit = None
            for i, p in enumerate(preds):
                if i in used:
                    continue
                if p.category not in ok_cats:
                    continue
                if lspan is not None and p.span is not None:
                    if not overlaps(p.span, lspan):
                        continue
                hit = i
                break
            if hit is None:
                by_cat[ltype]["fn"] += 1
            else:
                used.add(hit)
                by_cat[ltype]["tp"] += 1
                p = preds[hit]
                if lspan is not None and p.span is not None and overlaps(p.span, lspan):
                    by_cat[ltype]["localized"] += 1
                if p.category == ltype:
                    by_cat[ltype]["cat_ok"] += 1

        # Anything left over is a false positive, counted against its own category.
        for i, p in enumerate(preds):
            if i not in used:
                by_cat[p.category]["fp"] += 1

    per_cat = {}
    for cat, c in sorted(by_cat.items()):
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_cat[cat] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3),
            "localization": round(c["localized"] / tp, 3) if tp else None,
            "category_accuracy": round(c["cat_ok"] / tp, 3) if tp else None,
        }

    totals = {k: sum(v[k] for v in per_cat.values()) for k in ("tp", "fp", "fn")}
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    micro_p = tp / (tp + fp) if tp + fp else 0.0
    micro_r = tp / (tp + fn) if tp + fn else 0.0

    return {
        "overall": {
            "precision": round(micro_p, 3),
            "recall": round(micro_r, 3),
            "f1": round(2 * micro_p * micro_r / (micro_p + micro_r), 3) if micro_p + micro_r else 0.0,
            "false_alarm_rate": round(flagged_clean / len(clean_items), 3) if clean_items else 0.0,
            "clean_items": len(clean_items),
            "flagged_clean": flagged_clean,
            "labels_total": tp + fn,
            "predictions_total": tp + fp,
        },
        "per_category": per_cat,
    }


MIN_SAMPLES = 8   # below this a sweep is noise, not calibration
LO_RANGE = (0.40, 0.75)
HI_RANGE = (1.80, 3.00)


def sweep_length(records: list[dict]) -> dict:
    """Pick length thresholds that maximise F1 on labelled items.

    ``records`` are dicts with keys: ratio, label (omission/addition/None).
    Returns the chosen thresholds and the sweep table, so the choice is auditable.
    """
    n_omission = sum(1 for r in records if r["label"] == "omission")
    n_addition = sum(1 for r in records if r["label"] == "addition")
    best = {"lo": 0.55, "hi": 2.20, "lo_f1": 0.0, "hi_f1": 0.0,
            "lo_tuned": False, "hi_tuned": False,
            "n_omission": n_omission, "n_addition": n_addition}
    table = []

    lo_grid = [round(x * 0.05, 2) for x in range(6, 17)
               if LO_RANGE[0] <= round(x * 0.05, 2) <= LO_RANGE[1]]
    for lo in lo_grid:
        tp = fp = fn = 0
        for r in records:
            pred = r["ratio"] < lo
            truth = r["label"] == "omission"
            if pred and truth:
                tp += 1
            elif pred and not truth:
                fp += 1
            elif truth and not pred:
                fn += 1
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        table.append({"lo": lo, "precision": round(prec, 3), "recall": round(rec, 3),
                      "f1": round(f1, 3)})
        if f1 > best["lo_f1"]:
            best.update(lo=lo, lo_f1=f1, lo_tuned=True)

    hi_grid = [round(1.8 + x * 0.1, 2) for x in range(0, 13)
               if HI_RANGE[0] <= round(1.8 + x * 0.1, 2) <= HI_RANGE[1]]
    for hi in hi_grid:
        tp = fp = fn = 0
        for r in records:
            pred = r["ratio"] > hi
            truth = r["label"] == "addition"
            if pred and truth:
                tp += 1
            elif pred and not truth:
                fp += 1
            elif truth and not pred:
                fn += 1
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if f1 > best["hi_f1"]:
            best.update(hi=hi, hi_f1=f1, hi_tuned=True)

    # Refuse to trust a sweep fitted on a handful of examples.
    if n_omission < MIN_SAMPLES:
        best.update(lo=0.55, lo_tuned=False)
    if n_addition < MIN_SAMPLES:
        best.update(hi=2.20, hi_tuned=False)

    return {"chosen": best, "lo_sweep": table,
            "note": "thresholds chosen on labelled data only; rerun after adding labels"}
