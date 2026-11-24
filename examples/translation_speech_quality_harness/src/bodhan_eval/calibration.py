"""Calibration against labels (``--calibration labels.jsonl``).

* Labels: rows with ``id`` and ``labels``; label types go through the alias table
  (unknown types become ``unmapped:<raw>`` and are counted, never fatal).
* Split by item id (stable hash): train ~70%, held-out ~30%. Thresholds are tuned on
  train only; every reported metric is held-out only.
* Tuned knob: the judge confidence cutoff, globally, and per category only when that
  category has >= MIN_CATEGORY_POSITIVES train labels. Objective: best item-level F1
  with clean-item false alarms <= MAX_FALSE_ALARM.
* Every rate carries a Wilson 95% interval.

Definitions (also written into calibration.json):
  recall (item)      error items with >= 1 counted flag / error items
  per-label hit      a counted flag overlaps the label span (same frame); a label without
                     a usable span is hit by a counted flag of its category
  precision (item)   flagged items that carry a label / flagged items
  false alarm strict items without injected/human labels (reviewed_preexisting-only
                     items count as clean) that are flagged / those items
  false alarm reviewed  items with no labels at all that are flagged / those items
  localization       labels with a usable span on detected items: a counted flag overlaps
  category accuracy  labels on detected items: a counted flag has the label's category
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .config import PER_CATEGORY_PREFIX, TEXT_CATEGORIES
from .dataset import Label, parse_labels
from .infra import rate
from .judge import strong_flags, weak_flags
from .text import iou, overlaps

# -------------------- calibration
log = logging.getLogger("bodhan_eval.calibration")


TRAIN_SHARE = 7  # of 10 hash buckets


MIN_CATEGORY_POSITIVES = 10


MAX_FALSE_ALARM = 0.10


CANDIDATES = [round(0.1 * i, 1) for i in range(10)]  # judge confidence cutoffs 0.0 .. 0.9


PREEXISTING = "reviewed_preexisting"


def split_of(item_id: str) -> str:
    bucket = int(hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:8], 16) % 10
    return "train" if bucket < TRAIN_SHARE else "heldout"


def load_labels(path: Path) -> tuple[dict[str, list[Label]], dict]:
    labels: dict[str, list[Label]] = {}
    info = {"rows": 0, "bad_rows": 0, "bad_labels": 0, "rows_without_labels_field": 0, "labels": 0, "by_source": {}, "unmapped": {}}
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            info["rows"] += 1
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    raise ValueError("row needs a string 'id'")
                if row.get("labels") is None:
                    info["rows_without_labels_field"] += 1
                    continue
                if not isinstance(row["labels"], list):
                    raise ValueError("'labels' must be a list")
                parsed, label_errors = parse_labels(row["labels"])
            except (ValueError, TypeError) as exc:
                info["bad_rows"] += 1
                log.warning("bad_label_row", extra={"fields": {"error": str(exc)[:200]}})
                continue
            for err in label_errors:  # a bad label is dropped on its own; the row's other labels are kept
                info["bad_labels"] += 1
                log.warning("bad_label", extra={"fields": {"id": row["id"], "error": err[:200]}})
            if parsed is None:  # every label was bad: unknown, not "clean"
                continue
            labels[row["id"]] = parsed
            for lb in parsed:
                info["labels"] += 1
                src = str(lb.extra.get("source") or "unspecified")
                info["by_source"][src] = info["by_source"].get(src, 0) + 1
                if lb.category.startswith("unmapped:"):
                    info["unmapped"][lb.category] = info["unmapped"].get(lb.category, 0) + 1
    info["by_source"] = dict(sorted(info["by_source"].items()))
    info["unmapped"] = dict(sorted(info["unmapped"].items()))
    return labels, info


def _usable_span(lb: Label):
    if lb.span is None or lb.target_field == "source":
        return None
    return lb.span


def _frame_ok(lb: Label, flag: dict) -> bool:
    if lb.frame == "chars_or_seconds":
        return True
    return flag["unit"] == lb.frame


def label_result(lb: Label, flags: list[dict]) -> dict:
    span = _usable_span(lb)
    same_frame = [f for f in flags if f["span"] is not None and _frame_ok(lb, f)]
    best_iou = max((iou(tuple(f["span"]), span) for f in same_frame), default=0.0) if span else None
    localized = any(overlaps(tuple(f["span"]), span) for f in same_frame) if span else None
    category_hit = any(f["category"] == lb.category for f in flags)
    hit = localized if span else category_hit
    return {"has_span": span is not None, "localized": localized, "iou": best_iou, "category_hit": category_hit, "hit": bool(hit)}


def _clean_strict(labels: list[Label]) -> bool:
    return all(str(lb.extra.get("source")) == PREEXISTING for lb in labels)


def evaluate(items: list[dict], labels: dict[str, list[Label]], thresholds: dict, langs: list[str]) -> dict:
    """Metrics for ``items`` (already restricted to one split) with fusion at ``thresholds``."""
    acc = {"overall": _empty(), "by_lang": {lg: _empty() for lg in sorted(langs)}, "by_category": {}}
    extra_flags: dict[str, int] = {}
    for it in items:
        lbs = labels[it["id"]]
        flags = strong_flags(it, thresholds)
        weak = weak_flags(it, thresholds)
        buckets = [acc["overall"], acc["by_lang"].setdefault(it["lang"], _empty())]
        flagged = bool(flags)
        is_error = bool(lbs)
        for b in buckets:
            b["items"] += 1
            b["flagged"] += flagged
            b["flagged_with_label"] += flagged and is_error
            if is_error:
                b["error_items"] += 1
                b["detected_items"] += flagged
            if _clean_strict(lbs):
                b["clean_strict"] += 1
                b["clean_strict_flagged"] += flagged
            if not lbs:
                b["clean_reviewed"] += 1
                b["clean_reviewed_flagged"] += flagged
        matched_flags: set[int] = set()
        for lb in lbs:
            r = label_result(lb, flags)
            cat = acc["by_category"].setdefault(lb.category, _empty_cat())
            cat["labels"] += 1
            cat["hits"] += r["hit"]
            cat["weak_only_hits"] += (not r["hit"]) and label_result(lb, weak)["hit"]  # caught only by a weak signal
            for b in buckets + [cat]:
                b["labels_total"] += 1
                b["labels_hit"] += r["hit"]
                if flagged:
                    b["labels_on_detected"] += 1
                    b["category_hits"] += r["category_hit"]
                    if r["has_span"]:
                        b["spanned_on_detected"] += 1
                        b["localized"] += bool(r["localized"])
                        b["iou_sum"] += r["iou"] or 0.0
            for k, f in enumerate(flags):
                span = _usable_span(lb)
                if f["category"] == lb.category or (span and f["span"] is not None and _frame_ok(lb, f) and overlaps(tuple(f["span"]), span)):
                    matched_flags.add(k)
        label_cats = {lb.category for lb in lbs}
        for k, f in enumerate(flags):
            cat = acc["by_category"].setdefault(f["category"], _empty_cat())
            cat["flagged_items"] += 1
            cat["flagged_items_with_label"] += f["category"] in label_cats
            if is_error and k not in matched_flags:
                extra_flags[f["category"]] = extra_flags.get(f["category"], 0) + 1
    out = {
        "overall": _finish(acc["overall"]),
        "by_lang": {lg: _finish(b) for lg, b in sorted(acc["by_lang"].items())},
        "by_category": {c: _finish_cat(b) for c, b in sorted(acc["by_category"].items())},
        "extra_flags_on_error_items_by_category": dict(sorted(extra_flags.items())),
    }
    return out


def _empty() -> dict:
    keys = ("items", "flagged", "flagged_with_label", "error_items", "detected_items", "clean_strict", "clean_strict_flagged",
            "clean_reviewed", "clean_reviewed_flagged", "labels_total", "labels_hit", "labels_on_detected", "category_hits",
            "spanned_on_detected", "localized")
    d = {k: 0 for k in keys}
    d["iou_sum"] = 0.0
    return d


def _empty_cat() -> dict:
    d = _empty()
    d.update({"labels": 0, "hits": 0, "weak_only_hits": 0, "flagged_items": 0, "flagged_items_with_label": 0})
    return d


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None or p + r == 0:
        return None if p is None or r is None else 0.0
    return 2 * p * r / (p + r)


def _finish(b: dict) -> dict:
    precision = rate(b["flagged_with_label"], b["flagged"])
    recall = rate(b["detected_items"], b["error_items"])
    return {
        "items": b["items"],
        "recall_items": recall,
        "recall_labels": rate(b["labels_hit"], b["labels_total"]),
        "precision_items": precision,
        "f1_items": _f1(precision["value"], recall["value"]),
        "false_alarm_strict": rate(b["clean_strict_flagged"], b["clean_strict"]),
        "false_alarm_reviewed": rate(b["clean_reviewed_flagged"], b["clean_reviewed"]),
        "localization": rate(b["localized"], b["spanned_on_detected"]),
        "mean_iou": (b["iou_sum"] / b["spanned_on_detected"]) if b["spanned_on_detected"] else None,
        "category_accuracy": rate(b["category_hits"], b["labels_on_detected"]),
    }


def _finish_cat(b: dict) -> dict:
    precision = rate(b["flagged_items_with_label"], b["flagged_items"])
    recall = rate(b["hits"], b["labels"])
    return {
        "labels": b["labels"],
        "recall": recall,
        "weak_only": rate(b["weak_only_hits"], b["labels"]),  # missed by counted flags, found by a weak / review-queue flag
        "precision": precision,
        "f1": _f1(precision["value"], recall["value"]),
        "localization": rate(b["localized"], b["spanned_on_detected"]),
        "mean_iou": (b["iou_sum"] / b["spanned_on_detected"]) if b["spanned_on_detected"] else None,
        "category_accuracy": rate(b["category_hits"], b["labels_on_detected"]),
    }


def _score(m: dict) -> tuple:
    """Objective: satisfy the false-alarm cap first, then the best F1."""
    fa = m["overall"]["false_alarm_reviewed"]["value"]
    fa = 0.0 if fa is None else fa
    f1 = m["overall"]["f1_items"] or 0.0
    return (fa <= MAX_FALSE_ALARM, f1 if fa <= MAX_FALSE_ALARM else -fa)


def _sweep(train: list[dict], labels, base: dict, key: str, langs) -> tuple[float, list[dict]]:
    """Move the cutoff away from its current value only if another one is strictly better on
    train (a flat curve, e.g. the judge never reports that category, keeps the default)."""
    best = base[key] if key in base else base["judge_min_confidence"]
    best_score = _score(evaluate(train, labels, dict(base) | {key: best}, langs))
    curve = []
    for c in CANDIDATES:
        th = dict(base)
        th[key] = c
        m = evaluate(train, labels, th, langs)
        s = _score(m)
        curve.append({"cutoff": c, "f1": m["overall"]["f1_items"], "false_alarm": m["overall"]["false_alarm_reviewed"]["value"],
                      "recall": m["overall"]["recall_items"]["value"]})
        if s > best_score:
            best, best_score = c, s
    return best, curve


def calibrate(results: list[dict], labels: dict[str, list[Label]], info: dict, base_thresholds: dict, langs: list[str]) -> tuple[dict, dict, dict]:
    """Returns (thresholds to use for fusion, thresholds.json content, calibration.json content)."""
    run_ids = {r["id"] for r in results}
    labelled = [r for r in results if r["id"] in labels]
    train = [r for r in labelled if split_of(r["id"]) == "train"]
    heldout = [r for r in labelled if split_of(r["id"]) == "heldout"]
    has_judge_errors = any((r.get("judge") or {}).get("errors") for r in train)
    th = dict(base_thresholds)
    derivation: dict = {"split": "sha256(id) bucket mod 10 < 7 -> train", "train_items": len(train), "heldout_items": len(heldout),
                        "objective": f"max item F1 with reviewed false alarms <= {MAX_FALSE_ALARM:.2f} (train only)"}
    positives: dict[str, int] = {}
    for r in train:
        for lb in labels[r["id"]]:
            positives[lb.category] = positives.get(lb.category, 0) + 1
    derivation["train_positives_by_category"] = dict(sorted(positives.items()))
    if not has_judge_errors:
        derivation["status"] = "defaults: no judge errors on train items (judge not run or nothing found)"
    else:
        th["judge_min_confidence"], curve = _sweep(train, labels, th, "judge_min_confidence", langs)
        derivation["global_curve"] = curve
        per_cat = {}
        for cat in TEXT_CATEGORIES:
            if positives.get(cat, 0) >= MIN_CATEGORY_POSITIVES:
                key = PER_CATEGORY_PREFIX + cat
                th[key], _ = _sweep(train, labels, th, key, langs)
                per_cat[cat] = th[key]
        derivation["per_category"] = per_cat
        derivation["per_category_rule"] = f"only categories with >= {MIN_CATEGORY_POSITIVES} train labels; others use the global cutoff"
        derivation["status"] = "tuned"
    tuned = {k: v for k, v in sorted(th.items()) if k == "judge_min_confidence" or k.startswith(PER_CATEGORY_PREFIX)}
    thresholds_json = {"thresholds": tuned, "derived_from": derivation}
    calibration_json = {
        "status": "ok",
        "labels": info | {"items_labelled": len(labels), "labelled_items_not_in_run": len(set(labels) - run_ids)},
        "split": {"train": len(train), "heldout": len(heldout)},
        "thresholds": tuned,
        "heldout": evaluate(heldout, labels, th, langs),
        "judge_fluency_bias_heldout": judge_bias(heldout, labels, langs),
        "definitions": __doc__.split("Definitions (also written into calibration.json):", 1)[1].strip(),
    }
    return th, thresholds_json, calibration_json


def judge_bias(items: list[dict], labels: dict[str, list[Label]], langs: list[str]) -> dict:
    """The judge's own verdict on clean items (no labels): a fluency-bias measure, per language."""
    out = {}
    for lg in sorted(langs):
        judged = [r for r in items if r["lang"] == lg and not labels[r["id"]] and (r.get("judge") or {}).get("status") == "ran"]
        out[lg] = rate(sum(r["judge"]["verdict"] == "errors" for r in judged), len(judged))
    return out
