"""Calibration against labelled items: per-category precision/recall, headline targets, and
threshold tuning (coordinate descent on micro-F1 over (item, category) pairs)."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from .align import overlap
from .decide import DEFAULTS, TUNE_GRID

ALIASES = {
    "omit": "omission", "missing": "omission", "add": "addition", "insertion": "addition",
    "mistranslate": "mistranslation", "wrong_word": "mistranslation", "semantic": "mistranslation",
    "number": "number", "numeric": "number", "unit": "number", "date": "number", "quantity": "number",
    "entity": "term", "terminology": "term", "name": "term", "term": "term",
    "markdown": "format", "latex": "format", "table": "format", "formatting": "format",
    "language": "wrong_language", "script": "wrong_language", "untranslated": "wrong_language",
    "truncation": "speech_omission", "speech_omission": "speech_omission", "audio_omission": "speech_omission",
    "repeat": "repetition", "loop": "repetition", "noise": "garbled", "silence": "garbled", "unintelligible": "garbled",
}
SPEECH_CATS = {"speech_omission", "repetition", "garbled"}


def canon_label(lb: dict) -> dict | None:
    t = str(lb.get("type", "")).strip().lower().replace(" ", "_").replace("-", "_")
    cat = ALIASES.get(t, t)
    if cat == "omission" and (lb.get("area") == "speech" or lb.get("modality") == "speech"):
        cat = "speech_omission"
    if not cat:
        return None
    span = lb.get("span")
    return {"category": cat, "severity": lb.get("severity"), "span": span if isinstance(span, (list, tuple)) and len(span) == 2 else None,
            "label_source": lb.get("label_source", lb.get("source"))}


def flag_spans(f: dict):
    return [s for s in (f.get("span_source"), f.get("span_translation")) if s]


def evaluate(results: list[dict]) -> dict:
    """results: item dicts with `flags` and `labels` (canonical list, or None if unlabelled)."""
    known = [r for r in results if r.get("labels") is not None and r.get("status") == "ok"]
    err_items = [r for r in known if r["labels"]]
    clean_items = [r for r in known if not r["labels"]]
    pair = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    detected = loc_ok = loc_n = cat_ok = cat_n = 0
    for r in known:
        fcats = {f["category"] for f in r["flags"]}
        lcats = {l["category"] for l in r["labels"]}
        for c in fcats | lcats:
            k = "tp" if (c in fcats and c in lcats) else ("fp" if c in fcats else "fn")
            pair[c][k] += 1
        if r["labels"] and r["flags"]:
            detected += 1
        for l in r["labels"]:
            if r["flags"]:
                cat_n += 1
                cat_ok += l["category"] in fcats
            if l["category"] in fcats and l["span"]:
                loc_n += 1
                loc_ok += any(overlap(l["span"], s) > 0 for f in r["flags"] if f["category"] == l["category"] for s in flag_spans(f))
    per_cat = {}
    for c, d in sorted(pair.items()):
        p = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else None
        rc = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else None
        per_cat[c] = {**d, "precision": None if p is None else round(p, 4), "recall": None if rc is None else round(rc, 4)}
    tp = sum(d["tp"] for d in pair.values()); fp = sum(d["fp"] for d in pair.values()); fn = sum(d["fn"] for d in pair.values())
    f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
    ratio = lambda a, b: None if not b else round(a / b, 4)
    return {
        "n_labelled_items": len(known), "n_error_items": len(err_items), "n_clean_items": len(clean_items),
        "per_category": per_cat,
        "detection_recall": ratio(detected, len(err_items)),
        "false_alarm_rate": ratio(sum(1 for r in clean_items if r["flags"]), len(clean_items)),
        "localization_rate": ratio(loc_ok, loc_n),
        "category_accuracy": ratio(cat_ok, cat_n),
        "micro_f1": round(f1, 4),
    }


def tune(items: list[dict], decide_fn: Callable[[dict, dict], tuple], base: dict | None = None, min_labelled: int = 8, rounds: int = 2):
    """items: dicts with `signals`, `labels`, `status`. Returns (thresholds, report)."""
    thr = dict(base or DEFAULTS)
    labelled = [r for r in items if r.get("labels") is not None and r.get("status") == "ok"]
    if len(labelled) < min_labelled:
        return thr, {"tuned": False, "reason": f"only {len(labelled)} labelled items (< {min_labelled}); defaults kept"}

    def score(t):
        res = []
        for r in labelled:
            flags, _ = decide_fn(r, t)
            res.append({"status": "ok", "labels": r["labels"], "flags": flags})
        m = evaluate(res)
        fa = m["false_alarm_rate"] or 0.0
        return m["micro_f1"] - 0.25 * fa, m

    best, _ = score(thr)
    start = best
    changed = {}
    for _ in range(rounds):
        for k, grid in TUNE_GRID.items():
            for v in grid:
                if v == thr[k]:
                    continue
                s, _ = score({**thr, k: v})
                if s > best + 1e-9:  # strict improvement; ties keep the generic default
                    best, thr[k] = s, v
                    changed[k] = v
    return thr, {"tuned": True, "n_labelled": len(labelled), "objective_before": round(start, 4),
                 "objective_after": round(best, 4), "changed": changed}
