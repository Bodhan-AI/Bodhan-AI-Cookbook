"""Calibration against human labels: per-category thresholds and precision/recall.

Items with a ``labels`` field (an empty list means "labelled clean") are the calibration set.
Thresholds are tuned per category to maximise item-level F1 on that set; categories with
fewer than ``MIN_POSITIVES`` labelled examples keep the shipped default.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from bodhan_eval.flags import CATEGORIES, SPEECH_CATEGORIES, overlaps

# Defaults used when no labels are available. Exact checks fire at >= 0.6; judge "minor" (0.55) needs corroboration.
DEFAULT_THRESHOLDS = {c: 0.6 for c in CATEGORIES}
DEFAULT_THRESHOLDS.update({"omission": 0.6, "speech_omission": 0.4, "speech_repetition": 0.5, "speech_garbled": 0.6})
MIN_POSITIVES = 3
MIN_HOLDOUT = 40  # labelled items needed before splitting into tune / held-out halves


def _area(category: str) -> str:
    return "speech" if category in SPEECH_CATEGORIES else "translation"


def _f1(tp: int, fp: int, fn: int) -> float:
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def tune_thresholds(labelled: list[tuple[list[dict], list[dict]]]) -> tuple[dict[str, float], dict]:
    """labelled: (clusters, labels) per item. Returns (thresholds, tuning notes)."""
    thresholds, notes = dict(DEFAULT_THRESHOLDS), {}
    for cat in CATEGORIES:
        rows = []  # (best cluster score for cat or 0, item has label of cat)
        for clusters, labels in labelled:
            best = max((c["score"] for c in clusters if c["category"] == cat), default=0.0)
            rows.append((best, any(lab["category"] == cat for lab in labels)))
        positives = sum(p for _, p in rows)
        if positives < MIN_POSITIVES:
            notes[cat] = {"threshold": thresholds[cat], "source": "default", "labelled_positives": positives}
            continue
        candidates = sorted({s for s, _ in rows if s > 0} | {DEFAULT_THRESHOLDS[cat]})
        best_t, best_f1 = thresholds[cat], -1.0
        for t in candidates:
            tp = sum(1 for s, p in rows if p and s >= t)
            fp = sum(1 for s, p in rows if not p and s >= t)
            fn = positives - tp
            f1 = _f1(tp, fp, fn)
            if f1 > best_f1 + 1e-9:  # ties keep the lower threshold (favours recall)
                best_t, best_f1 = t, f1
        # Margin: every threshold in (highest score below best_t, best_t] gives the same F1 on these labels.
        # Sitting at best_t puts the cut right on the weakest positive; the midpoint generalizes better.
        below = [s for s, _ in rows if 0 < s < best_t]
        above = [s for s, _ in rows if s >= best_t]
        if below and above:
            best_t = (max(below) + min(above)) / 2
        thresholds[cat] = round(best_t, 3)
        notes[cat] = {"threshold": thresholds[cat], "source": "tuned", "labelled_positives": positives, "item_f1": round(best_f1, 3)}
    return thresholds, notes


def _flag_span(flag: dict, ref: str):
    s = flag["source_span"] if ref == "source" else flag["span"]
    return tuple(s) if s else None


def _ratio(a, b):
    return round(a / b, 3) if b else None


def evaluate(rows: list[dict]) -> dict:
    """rows: {"lang", "flags", "labels"} for labelled items. The assignment's target metrics, overall and
    per language, plus the diagnostics needed to explain a missed target."""
    per_cat = {c: {"tp": 0, "fp": 0, "fn": 0} for c in CATEGORIES}
    keys = ("labels", "detected", "localizable", "localized", "right_category", "clean", "clean_flagged")
    per_lang = defaultdict(lambda: dict.fromkeys(keys, 0))
    total_by_lang = dict.fromkeys(keys, 0)
    missed, false_alarm_cats, unlocalized, confusions = Counter(), Counter(), Counter(), Counter()

    for row in rows:
        flags, labels, lang = row["flags"], row["labels"], row["lang"]
        counts = [per_lang[lang], total_by_lang]
        if not labels:
            for c in counts:
                c["clean"] += 1
            if flags:
                for c in counts:
                    c["clean_flagged"] += 1
                false_alarm_cats.update({f["category"] for f in flags})
        for lab in labels:
            for c in counts:
                c["labels"] += 1
            same_area = [f for f in flags if _area(f["category"]) == _area(lab["category"])]
            if not same_area:
                missed[lab["category"]] += 1
                continue
            hit_cat = any(f["category"] == lab["category"] for f in same_area)
            for c in counts:
                c["detected"] += 1
                c["right_category"] += hit_cat
            if not hit_cat:
                confusions[f"{lab['category']} -> {same_area[0]['category']}"] += 1
            if lab["span"]:
                ok = any(overlaps(_flag_span(f, lab["ref"]), lab["span"]) for f in same_area)
                for c in counts:
                    c["localizable"] += 1
                    c["localized"] += ok
                if not ok:
                    unlocalized[lab["category"]] += 1
        # Category precision/recall at item level.
        flagged_cats = {f["category"] for f in flags}
        labelled_cats = {lab["category"] for lab in labels}
        for c in CATEGORIES:
            per_cat[c]["tp"] += c in flagged_cats and c in labelled_cats
            per_cat[c]["fp"] += c in flagged_cats and c not in labelled_cats
            per_cat[c]["fn"] += c in labelled_cats and c not in flagged_cats

    def summarize(m: dict) -> dict:
        return {
            "detection_recall": _ratio(m["detected"], m["labels"]),
            "false_alarm_rate": _ratio(m["clean_flagged"], m["clean"]),
            "localization": _ratio(m["localized"], m["localizable"]),
            "category_accuracy": _ratio(m["right_category"], m["detected"]),
            "seeded_errors": m["labels"],
            "clean_items": m["clean"],
        }

    categories = {}
    for c, m in per_cat.items():
        if m["tp"] + m["fp"] + m["fn"] == 0:
            continue
        categories[c] = {**m, "precision": _ratio(m["tp"], m["tp"] + m["fp"]), "recall": _ratio(m["tp"], m["tp"] + m["fn"])}
    return {
        "labelled_items": len(rows),
        **summarize(total_by_lang),
        "per_category": categories,
        "per_language": {lang: summarize(m) for lang, m in sorted(per_lang.items())},
        "diagnostics": {
            "missed_by_category": dict(missed.most_common()),
            "false_alarms_by_category": dict(false_alarm_cats.most_common()),
            "unlocalized_by_category": dict(unlocalized.most_common()),
            "category_confusions": dict(confusions.most_common()),
        },
    }


def evaluate_checks(rows: list[dict]) -> dict:
    """Each check and the judge on its own, against the labels: item-level precision/recall per
    category for any candidate the check emitted (before merging and thresholds). Shows which
    signal carries which category and how noisy it is."""
    checks = {c["check"] for row in rows for c in row["candidates"]}
    stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for row in rows:
        labelled = {lab["category"] for lab in row["labels"]}
        fired = {(c["check"], c["category"]) for c in row["candidates"]}
        for check in checks:
            for cat in labelled | {ca for ch, ca in fired if ch == check}:
                hit = (check, cat) in fired
                stats[(check, cat)]["tp"] += hit and cat in labelled
                stats[(check, cat)]["fp"] += hit and cat not in labelled
                stats[(check, cat)]["fn"] += not hit and cat in labelled
    out = defaultdict(dict)
    for (check, cat), m in sorted(stats.items()):
        if m["tp"] + m["fp"] == 0:
            continue  # this check never claims this category
        out[check][cat] = {**m, "precision": _ratio(m["tp"], m["tp"] + m["fp"]), "recall": _ratio(m["tp"], m["tp"] + m["fn"])}
    return dict(out)


def human_review(judgments: list[dict], clusters_by_item: dict[tuple[str, str], list[dict]], thresholds: dict[str, float]) -> dict:
    """Score the harness against a person's verdicts on individual flags.

    A verdict is about one flagged category of one item. The harness "flagged" it when the best cluster
    of that category scored at or above its threshold. error+flagged is a true positive, clean+flagged a
    false alarm, error+unflagged a miss. The queue holds near-threshold flags, so this sample is harder
    than average: it is an audit of the uncertain region, not a replacement for the injector metrics."""
    per_cat = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    skipped = caught_elsewhere = 0
    for j in judgments:
        clusters = clusters_by_item.get((j["id"], j["lang"]))
        if clusters is None:
            skipped += 1
            continue
        best = max((c["score"] for c in clusters if c["category"] == j["category"]), default=0.0)
        flagged = best >= thresholds.get(j["category"], 0.5)
        is_error = j["verdict"] == "error"
        cell = ("tp" if is_error else "fp") if flagged else ("fn" if is_error else "tn")
        per_cat[j["category"]][cell] += 1
        if cell == "fn" and any(c["score"] >= thresholds.get(c["category"], 0.5) and _area(c["category"]) == _area(j["category"])
                                for c in clusters):
            caught_elsewhere += 1  # a real error the harness did flag, under another category (e.g. a dropped number as omission)
    summary = _human_summary(per_cat, len(judgments) - skipped, skipped, judgments)
    summary["missed_but_flagged_as_other_category"] = caught_elsewhere
    return summary


def _human_summary(per_cat: dict, scored: int, skipped: int, judgments: list[dict]) -> dict:
    cats = {}
    totals = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for c, m in sorted(per_cat.items()):
        cats[c] = {**m, "precision": _ratio(m["tp"], m["tp"] + m["fp"]), "recall": _ratio(m["tp"], m["tp"] + m["fn"])}
        for k in totals:
            totals[k] += m[k]
    return {
        "judgments": scored, "skipped_not_in_run": skipped,
        "annotators": dict(Counter(j["annotator"] for j in judgments)),
        "errors_confirmed": totals["tp"] + totals["fn"], "false_alarms_confirmed": totals["fp"] + totals["tn"],
        "precision": _ratio(totals["tp"], totals["tp"] + totals["fp"]), "recall": _ratio(totals["tp"], totals["tp"] + totals["fn"]),
        "agreement": _ratio(totals["tp"] + totals["tn"], scored), **totals, "per_category": cats,
    }


def explain_misses(metrics: dict, consistency: dict) -> dict[str, str]:
    """One plain-English reason per missed target, from the evaluation diagnostics."""
    d = metrics.get("diagnostics", {})

    def top(counter: dict, n: int = 3) -> str:
        return ", ".join(f"{k} ({v})" for k, v in list(counter.items())[:n]) or "–"

    notes = {}
    for t in target_status(metrics, consistency.get("same_verdict_rate")):
        if t["met"] is not False:
            continue
        if t["metric"] == "detection_recall":
            notes[t["metric"]] = f"unflagged seeded errors are mostly {top(d.get('missed_by_category', {}))}"
        elif t["metric"] == "false_alarm_rate":
            notes[t["metric"]] = f"clean items are flagged mostly as {top(d.get('false_alarms_by_category', {}))}; inspect them, they may be real model errors"
        elif t["metric"] == "localization":
            notes[t["metric"]] = f"detections without an overlapping span are mostly {top(d.get('unlocalized_by_category', {}))}"
        elif t["metric"] == "category_accuracy":
            notes[t["metric"]] = f"most common confusions: {top(d.get('category_confusions', {}))}"
        elif t["metric"] == "judge_consistency":
            notes[t["metric"]] = f"per-language agreement: {consistency.get('per_language')}"
    return notes


TARGETS = {
    "detection_recall": (">=", 0.8),
    "false_alarm_rate": ("<=", 0.10),
    "localization": (">=", 0.7),
    "category_accuracy": (">=", 0.7),
    "judge_consistency": (">=", 0.9),
}


def target_status(metrics: dict, consistency: float | None) -> list[dict]:
    values = {**metrics, "judge_consistency": consistency}
    out = []
    for name, (op, target) in TARGETS.items():
        v = values.get(name)
        met = None if v is None else (v >= target if op == ">=" else v <= target)
        out.append({"metric": name, "value": v, "target": f"{op} {target}", "met": met})
    return out
