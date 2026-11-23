"""Calibration against labelled items: matching, metrics, and threshold tuning.

Matching a flag to a label
    A flag *localises* a label when their spans overlap (translation span vs
    label span, or source span vs label span when the label is on the source).
    A label is *detected* when some flag localises it, or, if neither side has a
    usable span, when a flag of the same category group exists on the item.
    The matched flag's category is *correct* when it equals the label's category
    (or is in the same group, e.g. generic "omission" vs "speech_omission").

Threshold tuning
    Because a cluster's category does not depend on thresholds, each category's
    threshold can be tuned on its own: for every value in ``threshold_grid`` we
    re-run fusion on the labelled items and keep the threshold with the best F1
    for that category (ties -> the higher, more conservative threshold).
    Categories with fewer than ``min_labels_to_tune`` labels keep the default.
    To avoid reporting in-sample optimism, metrics are also computed with 2-fold
    cross-validation (tune on one half of the labelled items, score the other).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field

from .config import CATEGORIES, category_area, category_group
from .fusion import fuse
from .models import Flag, ItemResult, Label, Signal
from .spans import overlaps


def _label_hits(lab: Label, f: Flag) -> bool:
    if lab.span is None:
        return False
    other = f.source_span if lab.span_on == "source" else f.span
    if overlaps(lab.span, other):
        return True
    # omission labels are often given on the source even when unannotated: accept either side
    return lab.category in ("omission", "speech_omission") and overlaps(
        lab.span, f.source_span if lab.span_on != "source" else f.span
    )


def _same_group(lab: Label, f: Flag) -> bool:
    if lab.category is None:
        return False
    return lab.category == f.category or category_group(lab.category) == category_group(f.category)


def match_item(flags: list[Flag], labels: list[Label]) -> tuple[list[dict], list[bool]]:
    """Per label: detected/localized/category_correct and the matched flag; per flag: whether it matched any label."""
    flag_used = [False] * len(flags)
    out = []
    for lab in labels:
        best, best_rank = None, -1
        for k, f in enumerate(flags):
            hit = _label_hits(lab, f)
            grp = _same_group(lab, f)
            same_area = lab.category is None or category_area(lab.category) == category_area(f.category)
            if hit and grp:
                rank = 4
            elif hit and same_area:
                rank = 3
            elif grp and (lab.span is None or (f.span is None and f.source_span is None)):
                rank = 2
            elif grp:
                rank = 1  # same kind of error on this item, different place
            else:
                continue
            if rank > best_rank:
                best, best_rank = k, rank
        res = {
            "category": lab.category,
            "raw_type": lab.raw_type,
            "detected": best is not None,
            "localized": None,
            "category_correct": None,
            "category_exact": None,
            "rank": best_rank,
        }
        if best is not None:
            f = flags[best]
            flag_used[best] = True
            res["localized"] = _label_hits(lab, f) if lab.span is not None else None
            res["category_correct"] = _same_group(lab, f)
            res["category_exact"] = lab.category == f.category
            res["flag_category"] = f.category
        out.append(res)
    return out, flag_used


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def evaluate(results: list[ItemResult]) -> dict:
    """Headline and per-category metrics over labelled items using their current flags."""
    lab_items = [r for r in results if r.item.labels is not None and r.status == "ok"]
    per_cat: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {
            "labels": 0,
            "detected": 0,
            "flags": 0,
            "flags_matched": 0,
            "localized": 0,
            "localizable": 0,
            "category_correct": 0,
        }
    )
    per_lang: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"labels": 0, "detected": 0, "clean_items": 0, "clean_flagged": 0}
    )
    tot = {
        "labels": 0,
        "detected": 0,
        "localized": 0,
        "localizable": 0,
        "cat_ok": 0,
        "cat_exact": 0,
        "clean": 0,
        "clean_flagged": 0,
        "err_items": 0,
        "err_items_flagged": 0,
        "flags": 0,
        "flags_matched": 0,
    }
    for r in lab_items:
        labels = r.item.labels or []
        matches, used = match_item(r.flags, labels)
        lang = r.item.lang
        if not labels:
            tot["clean"] += 1
            per_lang[lang]["clean_items"] += 1
            if r.flags:
                tot["clean_flagged"] += 1
                per_lang[lang]["clean_flagged"] += 1
        else:
            tot["err_items"] += 1
            tot["err_items_flagged"] += bool(r.flags)
        for f, u in zip(r.flags, used):
            per_cat[f.category]["flags"] += 1
            per_cat[f.category]["flags_matched"] += u
            tot["flags"] += 1
            tot["flags_matched"] += u
        for m in matches:
            cat = m["category"] or f"unmapped:{m['raw_type']}"
            pc = per_cat[cat]
            pc["labels"] += 1
            per_lang[lang]["labels"] += 1
            tot["labels"] += 1
            if m["detected"]:
                pc["detected"] += 1
                per_lang[lang]["detected"] += 1
                tot["detected"] += 1
                tot["cat_ok"] += bool(m["category_correct"])
                tot["cat_exact"] += bool(m["category_exact"])
                pc["category_correct"] += bool(m["category_correct"])
                if m["localized"] is not None:
                    pc["localizable"] += 1
                    tot["localizable"] += 1
                    pc["localized"] += bool(m["localized"])
                    tot["localized"] += bool(m["localized"])
    cats = {}
    for c, v in sorted(per_cat.items()):
        p = _ratio(v["flags_matched"], v["flags"])
        rcl = _ratio(v["detected"], v["labels"])
        f1 = round(2 * p * rcl / (p + rcl), 4) if p and rcl else (0.0 if v["labels"] or v["flags"] else None)
        cats[c] = {
            **v,
            "precision": p,
            "recall": rcl,
            "f1": f1,
            "localization": _ratio(v["localized"], v["localizable"]),
            "category_accuracy": _ratio(v["category_correct"], v["detected"]),
        }
    langs = {
        lang: {
            **v,
            "recall": _ratio(v["detected"], v["labels"]),
            "false_alarm_rate": _ratio(v["clean_flagged"], v["clean_items"]),
        }
        for lang, v in sorted(per_lang.items())
    }
    return {
        "labelled_items": len(lab_items),
        "labels": tot["labels"],
        "clean_items": tot["clean"],
        "recall": _ratio(tot["detected"], tot["labels"]),
        "item_recall": _ratio(tot["err_items_flagged"], tot["err_items"]),
        "false_alarm_rate": _ratio(tot["clean_flagged"], tot["clean"]),
        "precision": _ratio(tot["flags_matched"], tot["flags"]),
        "localization": _ratio(tot["localized"], tot["localizable"]),
        "category_accuracy": _ratio(tot["cat_ok"], tot["detected"]),
        "category_accuracy_exact": _ratio(tot["cat_exact"], tot["detected"]),
        "per_category": cats,
        "per_language": langs,
    }


@dataclass
class _CheckStats:
    """Running counts for one check in ``per_check``."""

    signals: int = 0
    matched: int = 0
    labels_hit: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))


def per_check(results: list[ItemResult], min_score: float = 0.3) -> dict:
    """Signal-level precision/recall of each check on its own (before fusion), against labels."""
    stats: defaultdict[str, _CheckStats] = defaultdict(_CheckStats)
    label_counts: dict[str, int] = defaultdict(int)
    for r in results:
        if r.item.labels is None or r.status != "ok":
            continue
        for lab in r.item.labels:
            label_counts[lab.category or "unmapped"] += 1
        by_check: defaultdict[str, list[Signal]] = defaultdict(list)
        for s in r.signals:
            if s.score >= min_score:
                by_check[s.check].append(s)
        for check, sigs in by_check.items():
            fake = [Flag(s.category, s.severity, s.score, s.span, s.source_span, None, None, [], [check]) for s in sigs]
            matches, used = match_item(fake, r.item.labels)
            stats[check].signals += len(fake)
            stats[check].matched += sum(used)
            for m in matches:
                if m["detected"] and m["rank"] >= 2:
                    stats[check].labels_hit[m["category"] or "unmapped"] += 1
    out = {}
    for check, v in sorted(stats.items()):
        out[check] = {
            "signals": v.signals,
            "precision": _ratio(v.matched, v.signals),
            "recall_by_category": {c: _ratio(n, label_counts[c]) for c, n in sorted(v.labels_hit.items())},
        }
    return out


def judge_consistency(results: list[ItemResult]) -> dict:
    """Order consistency (first two replicas, re-ordered prompts), tie-break rate, and repeat consistency
    (the identical prompt sent twice, on a sample) reported separately: they measure different things."""
    n = agree_bin = agree_cat = judged = tie_breaks = rep_n = rep_agree = 0
    per_lang: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        m = r.metrics
        if "judge_replicas" in m:
            judged += 1
            tie_breaks += bool(m.get("judge_tie_break"))
        if "judge_agree_binary" in m:
            n += 1
            agree_bin += bool(m["judge_agree_binary"])
            agree_cat += bool(m["judge_agree_categories"])
            per_lang[r.item.lang][0] += 1
            per_lang[r.item.lang][1] += bool(m["judge_agree_categories"])
        if "judge_repeat_agree" in m:
            rep_n += 1
            rep_agree += bool(m["judge_repeat_agree"])
    return {
        "items": n,
        "same_verdict_rate": _ratio(agree_bin, n),
        "same_categories_rate": _ratio(agree_cat, n),
        "per_language": {k: _ratio(v[1], v[0]) for k, v in sorted(per_lang.items())},
        "tie_break_rate": _ratio(tie_breaks, judged),
        "repeat": {"items": rep_n, "same_categories_rate": _ratio(rep_agree, rep_n)},
    }


def _fold(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2


def _refuse(results: list[ItemResult], thresholds: dict, margin: float) -> None:
    for r in results:
        r.flags, r.uncertain, _ = fuse(r.item, r.signals, thresholds, margin)


def tune(
    results: list[ItemResult], defaults: dict, grid: list[float], min_labels: int, margin: float
) -> tuple[dict, dict]:
    """Pick per-category thresholds from labelled items. Returns (thresholds, report)."""
    lab = [r for r in results if r.item.labels is not None and r.status == "ok"]
    thresholds = dict(defaults)
    report: dict = {
        "method": "per-category F1 grid search over fused scores; ties -> higher threshold",
        "grid": grid,
        "min_labels_to_tune": min_labels,
        "categories": {},
    }
    if not lab:
        report["note"] = "no labelled items: default thresholds used"
        return thresholds, report
    counts: defaultdict[str, int] = defaultdict(int)
    for r in lab:
        for label in r.item.labels or []:
            if label.category:
                counts[label.category] += 1
    for cat in CATEGORIES:
        if counts[cat] < min_labels:
            report["categories"][cat] = {
                "threshold": thresholds[cat],
                "tuned": False,
                "labels": counts[cat],
                "reason": f"fewer than {min_labels} labels",
            }
            continue
        curve = []
        for t in grid:
            _refuse(lab, {**thresholds, cat: t}, margin)
            m = evaluate(lab)["per_category"].get(cat, {})
            curve.append((m.get("f1") or 0.0, t, m.get("precision"), m.get("recall")))
        best = max(curve, key=lambda x: (x[0], x[1]))
        thresholds[cat] = best[1]
        report["categories"][cat] = {
            "threshold": best[1],
            "tuned": True,
            "labels": counts[cat],
            "f1": best[0],
            "precision": best[2],
            "recall": best[3],
            "curve": [{"t": t, "f1": f, "p": p, "r": rc} for f, t, p, rc in curve],
        }
    _refuse(lab, thresholds, margin)
    return thresholds, report


def cross_validated(
    results: list[ItemResult], defaults: dict, grid: list[float], min_labels: int, margin: float
) -> dict | None:
    lab = [r for r in results if r.item.labels is not None and r.status == "ok"]
    folds = [[r for r in lab if _fold(r.item.key) == k] for k in (0, 1)]
    if min(len(f) for f in folds) < 10:
        return None
    for k in (0, 1):
        th, _ = tune(folds[k], defaults, grid, min_labels, margin)
        _refuse(folds[1 - k], th, margin)
    return evaluate(folds[0] + folds[1])
