"""Calibration against labelled items: metrics, per-check precision/recall, and threshold tuning.

Definitions (also written into calibration.json):
* detection recall: a labelled error counts as detected if its item has at least one flag in the
  same area (translation or speech).
* localization: among detected errors with a span, the share where a flag's span overlaps it.
* category accuracy: among detected errors, the share where a flag has the right category.
* false-alarm rate: share of verified-clean items (labels == []) with any flag.
Thresholds are tuned per category to maximise F1 (ties -> middle of the best-F1 plateau, i.e. max margin), then raised until the
false-alarm rate is at most 10%. Reported "cv" metrics come from grouped k-fold cross-validation,
so they estimate performance on unseen items rather than on the tuning set.
"""
from __future__ import annotations

import copy
import random
from collections import defaultdict

from .fuse import Cluster, flags_for
from .taxonomy import CATEGORIES, area_of

# 0.30 .. 0.90. Capped below 1: a threshold above 0.9 would let calibration silence a deterministic check entirely.
GRID = [round(0.30 + 0.05 * i, 2) for i in range(13)]
TOL = 6  # chars of tolerance around zero-length (insertion-point) label spans


def _lspan(label, field_name: str):
    sp = label.span if (label.span_field == field_name) else (label.source_span if field_name == "source" else None)
    if sp is None and field_name == "source":
        sp = label.source_span
    if sp is None:
        return None
    a, b = sp
    return (a - TOL, b + TOL) if a == b else (a, b)


def _ov(a, b) -> bool:
    return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]


def localized(label, flag) -> bool | None:
    lt, ls = _lspan(label, "translation"), _lspan(label, "source")
    if lt is None and ls is None:
        return None
    return _ov(lt, flag["span"] and tuple(flag["span"])) or _ov(ls, flag["source_span"] and tuple(flag["source_span"]))


def evaluate(records: list[dict], cfg: dict) -> dict:
    """records: [{id, lang, labels, clusters, has_audio}] (labelled items only)."""
    per_cat = {c: {"tp": 0, "fp": 0, "fn": 0} for c in CATEGORIES}
    det = loc_n = loc_hit = cat_hit = 0
    n_labels = 0
    clean = clean_flagged = 0
    by_lang: dict[str, dict] = defaultdict(lambda: {"labels": 0, "detected": 0, "clean": 0, "clean_flagged": 0})
    by_cat_det: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in records:
        flags = flags_for(r["clusters"], cfg)
        lab_cats = {lb.category for lb in r["labels"]}
        flag_cats = {f["category"] for f in flags}
        for c in CATEGORIES:
            if c in flag_cats and c in lab_cats:
                per_cat[c]["tp"] += 1
            elif c in flag_cats:
                per_cat[c]["fp"] += 1
            elif c in lab_cats:
                per_cat[c]["fn"] += 1
        bl = by_lang[r["lang"]]
        if not r["labels"]:
            clean += 1
            bl["clean"] += 1
            if flags:
                clean_flagged += 1
                bl["clean_flagged"] += 1
            continue
        for lb in r["labels"]:
            n_labels += 1
            bl["labels"] += 1
            cat = lb.category
            by_cat_det[cat][1] += 1
            same_area = [f for f in flags if f["area"] == area_of(cat)]
            if not same_area:
                continue
            det += 1
            bl["detected"] += 1
            by_cat_det[cat][0] += 1
            if any(f["category"] == cat for f in same_area):
                cat_hit += 1
            hits = [localized(lb, f) for f in same_area]
            if any(h is not None for h in hits):
                loc_n += 1
                loc_hit += any(hits)

    def prf(d):
        p = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else None
        rc = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else None
        f1 = 2 * p * rc / (p + rc) if p and rc else (0.0 if p is not None and rc is not None else None)
        return {**d, "precision": _r(p), "recall": _r(rc), "f1": _r(f1)}

    return {
        "n_labelled_items": len(records),
        "n_errors": n_labels,
        "n_clean": clean,
        "detection_recall": _r(det / n_labels if n_labels else None),
        "false_alarm_rate": _r(clean_flagged / clean if clean else None),
        "localization": _r(loc_hit / loc_n if loc_n else None),
        "category_accuracy": _r(cat_hit / det if det else None),
        "per_category": {c: prf(d) | {"detection_recall": _r(by_cat_det[c][0] / by_cat_det[c][1] if by_cat_det[c][1] else None)}
                         for c, d in per_cat.items() if d["tp"] + d["fp"] + d["fn"]},
        "per_language": {lang: {"detection_recall": _r(v["detected"] / v["labels"] if v["labels"] else None),
                                "false_alarm_rate": _r(v["clean_flagged"] / v["clean"] if v["clean"] else None),
                                "errors": v["labels"], "clean": v["clean"]} for lang, v in sorted(by_lang.items())},
    }


def per_check(records: list[dict], min_conf: float = 0.5) -> dict:
    """Each check on its own (signal confidence >= min_conf counts as a prediction)."""
    stats: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"tp": 0, "fp": 0}))
    totals: dict[str, int] = defaultdict(int)
    for r in records:
        lab_cats = {lb.category for lb in r["labels"]}
        for c in lab_cats:
            totals[c] += 1
        seen = set()
        for cl in r["clusters"]:
            for s in cl.signals:
                if s.confidence < min_conf or (s.check, s.category) in seen:
                    continue
                seen.add((s.check, s.category))
                stats[s.check][s.category]["tp" if s.category in lab_cats else "fp"] += 1
    out = {}
    for check, cats in sorted(stats.items()):
        out[check] = {c: {"predictions": d["tp"] + d["fp"], "precision": _r(d["tp"] / (d["tp"] + d["fp"])),
                          "recall": _r(d["tp"] / totals[c] if totals.get(c) else None)} for c, d in sorted(cats.items())}
    return out


def tune(records: list[dict], cfg: dict, max_fa: float = 0.10) -> tuple[dict, dict]:
    """Return (tuned cfg, notes). Categories with too few labels keep their default threshold."""
    cfg = copy.deepcopy(cfg)
    min_labels = cfg.get("min_labels_to_retune", 6)
    notes = {}
    w, dw = cfg["weights"], cfg.get("deny_weight", 0.8)
    for c in CATEGORIES:
        positives = [r for r in records if any(lb.category == c for lb in r["labels"])]
        if len(positives) < min_labels:
            notes[c] = f"kept default {cfg['thresholds'][c]} ({len(positives)} labelled examples < {min_labels})"
            continue
        scores = []
        for r in records:
            best = max((cl.score(w, dw) for cl in r["clusters"] if cl.category == c), default=0.0)
            scores.append((best, any(lb.category == c for lb in r["labels"])))
        f1s = []
        for t in GRID:
            tp = sum(1 for s, y in scores if s >= t and y)
            fp = sum(1 for s, y in scores if s >= t and not y)
            fn = sum(1 for s, y in scores if s < t and y)
            f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
        best_f1 = max(f1s)
        plateau = [t for t, f in zip(GRID, f1s, strict=True) if f >= best_f1 - 1e-9]
        # max-margin: the middle of the best-F1 plateau, not its edge. With well-separated scores every threshold
        # in a wide range is "perfect" on the labels; the edge would reject anything slightly weaker than they were.
        best_t = plateau[len(plateau) // 2]
        cfg["thresholds"][c] = best_t
        notes[c] = f"tuned to {best_t} (F1 {best_f1:.2f} on {len(positives)} labelled examples)"
    # enforce the false-alarm budget on verified-clean items
    for _ in range(20):
        fa = evaluate(records, cfg)["false_alarm_rate"]
        if fa is None or fa <= max_fa:
            break
        blame: dict[str, int] = defaultdict(int)
        for r in records:
            if not r["labels"]:
                for f in flags_for(r["clusters"], cfg):
                    if cfg["thresholds"][f["category"]] < GRID[-1]:  # capped categories cannot be raised further
                        blame[f["category"]] += 1
        if not blame:
            notes["false_alarm_budget"] = (f"could not reach <= {max_fa:.0%}: remaining false alarms come from categories already at the "
                                           f"{GRID[-1]} cap (deterministic or very confident evidence)")
            break
        worst = max(blame, key=blame.get)
        cfg["thresholds"][worst] = round(min(GRID[-1], cfg["thresholds"][worst] + 0.05), 2)
        notes[worst] = notes.get(worst, "") + f"; raised to {cfg['thresholds'][worst]} to keep false alarms <= {max_fa:.0%}"
    return cfg, notes


def cross_validate(records: list[dict], cfg: dict, k: int = 5, seed: int = 13) -> dict | None:
    groups = sorted({r["group"] for r in records})
    if len(groups) < k * 2:
        return None
    rng = random.Random(seed)
    rng.shuffle(groups)
    fold_of = {g: i % k for i, g in enumerate(groups)}
    pooled: list[tuple[dict, dict]] = []
    for f in range(k):
        train = [r for r in records if fold_of[r["group"]] != f]
        test = [r for r in records if fold_of[r["group"]] == f]
        tuned, _ = tune(train, cfg)
        pooled.extend((r, tuned) for r in test)
    # evaluate each test item with the thresholds tuned without it
    by_cfg: dict[int, list[dict]] = defaultdict(list)
    cfgs: dict[int, dict] = {}
    for r, c in pooled:
        by_cfg[id(c)].append(r)
        cfgs[id(c)] = c
    parts = [evaluate(rs, cfgs[i]) for i, rs in by_cfg.items()]
    return _merge(parts)


def _merge(parts: list[dict]) -> dict:
    """Pool fold-level metrics, weighting by the counts they were computed from."""
    def wavg(key, weight_key):
        num = den = 0.0
        for p in parts:
            if p[key] is not None and p[weight_key]:
                num += p[key] * p[weight_key]
                den += p[weight_key]
        return _r(num / den) if den else None

    return {"detection_recall": wavg("detection_recall", "n_errors"), "false_alarm_rate": wavg("false_alarm_rate", "n_clean"),
            "category_accuracy": wavg("category_accuracy", "n_errors"), "localization": wavg("localization", "n_errors"),
            "folds": len(parts)}


def _r(x):
    return None if x is None else round(float(x), 4)


def records_from(results: list[dict], clusters_by_id: dict[str, list[Cluster]]) -> list[dict]:
    out = []
    for r in results:
        if r.get("labels") is None or r["status"] in ("skipped", "error"):
            continue
        out.append({"id": r["id"], "lang": r["lang"], "labels": r["labels"], "clusters": clusters_by_id.get(r["id"], []),
                    "group": r.get("group") or r["id"]})
    return out
