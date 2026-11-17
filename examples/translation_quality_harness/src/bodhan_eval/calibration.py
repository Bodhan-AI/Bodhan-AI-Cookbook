"""Score flags against labels and tune per-category thresholds from the labelled items.

Matching rules (documented in docs/DESIGN.md):
- A label is *detected* by a same-area flag that overlaps its span or has its category.
  Only labels with neither category nor span fall back to any same-area flag.
- *Localized*: some flag overlaps the label span. Label spans refer to the translation unless the
  label says `"field": "source"`; without a field, overlap with either side counts.
- Localization uses detected labels with spans, as specified by the assignment.
  Localization recall also reports overlap over all labels with spans, including misses.
- *Category correct*: a same-area flag of the label's category exists.
- A *false alarm* is a clean labelled item (labels == []) with at least one flag.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .config import Thresholds
from .fusion import _order, mqm
from .models import Category, Flag, ItemResult

TARGETS = {"detection_recall": 0.80, "false_alarm_rate": 0.10, "localization": 0.70, "category_accuracy": 0.70, "judge_consistency": 0.90}
SPAN_TOLERANCE = 2
MIN_SUPPORT = 3


@dataclass
class LabelView:
    category: Category | None
    ambiguous_omission: bool
    span: tuple[int, int] | None
    field: str | None
    area: str | None


def _labels(result: ItemResult) -> list[LabelView]:
    out = []
    for lb in result.labels:
        cat = Category(lb["category"]) if lb.get("category") else None
        ambiguous = str(lb.get("type", "")).strip().lower() == "omission" and not lb.get("area")
        span = tuple(lb["span"]) if lb.get("span") else None
        out.append(LabelView(cat, ambiguous, span, lb.get("field"), lb.get("area")))
    return out


def _widen(span: tuple[int, int]) -> tuple[int, int]:
    s, e = span
    return (s - SPAN_TOLERANCE, e + SPAN_TOLERANCE) if e - s <= 0 else (s, e)


def _hit(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    if a is None or b is None:
        return False
    a, b = _widen(a), _widen(b)
    return a[0] < b[1] and b[0] < a[1]


def overlaps(label: LabelView, flag: Flag) -> bool:
    if label.span is None:
        return False
    field = (label.field or "").lower()
    if field == "source":
        return _hit(label.span, flag.source_span)
    if field in {"translation", "target", "audio_text", "text"}:
        return _hit(label.span, flag.span)
    return _hit(label.span, flag.span) or _hit(label.span, flag.source_span)


def category_match(label: LabelView, flag: Flag) -> bool:
    if label.ambiguous_omission:
        return flag.category in (Category.OMISSION, Category.SPEECH_OMISSION)
    return label.category == flag.category


def _label_area(label: LabelView) -> str | None:
    if label.category is not None and not label.ambiguous_omission:
        return label.category.area
    return label.area


def decide(result: ItemResult, th: Thresholds) -> list[Flag]:
    pool = result.flags + result.rejected
    return [f for f in pool if f.confidence >= th.threshold(f.category)]


def apply_thresholds(result: ItemResult, th: Thresholds) -> ItemResult:
    pool = result.flags + result.rejected
    flags = _order([f for f in pool if f.confidence >= th.threshold(f.category)])
    rejected = _order([f for f in pool if f.confidence < th.threshold(f.category)])
    return result.model_copy(update={"flags": flags, "rejected": rejected})


def rescore(result: ItemResult, th: Thresholds, source: str) -> ItemResult:
    updated = apply_thresholds(result, th)
    penalty, score = mqm(updated.flags, source)
    return updated.model_copy(update={"mqm_penalty": penalty, "quality_score": score})


def evaluate(results: list[ItemResult], th: Thresholds, labeled_ids: set[str]) -> dict:
    items = [r for r in results if r.status == "scored" and r.id in labeled_ids]
    per_cat = defaultdict(lambda: {"labels": 0, "detected": 0, "correct_category": 0, "flags": 0, "true_flags": 0})
    per_lang = defaultdict(lambda: {"labels": 0, "detected": 0, "clean": 0, "false_alarms": 0, "localized": 0, "with_span": 0,
                                    "detected_with_span": 0, "correct_category": 0})
    per_check = defaultdict(lambda: {"flags": 0, "true_flags": 0})
    totals = {"labels": 0, "detected": 0, "with_span": 0, "detected_with_span": 0, "localized": 0,
              "correct_category": 0, "clean": 0, "false_alarms": 0}
    judge = {"judge_only_flags": 0, "judge_only_true": 0, "confirmed_flags": 0, "confirmed_true": 0, "rejected_candidates": 0, "rejected_but_labeled": 0}

    for r in items:
        flags = decide(r, th)
        labels = _labels(r)
        lang = per_lang[r.lang]
        if not labels:
            totals["clean"] += 1
            lang["clean"] += 1
            if flags:
                totals["false_alarms"] += 1
                lang["false_alarms"] += 1
        for lb in labels:
            area = _label_area(lb)
            same_area = [f for f in flags if area is None or f.area == area]
            over = [f for f in same_area if overlaps(lb, f)]
            cat_flags = [f for f in same_area if category_match(lb, f)]
            detected = bool(over or cat_flags or (lb.span is None and lb.category is None and same_area))
            key = lb.category.value if lb.category else "unknown"
            if lb.ambiguous_omission:
                key = "omission"
            totals["labels"] += 1
            lang["labels"] += 1
            per_cat[key]["labels"] += 1
            if lb.span is not None:
                totals["with_span"] += 1
                lang["with_span"] += 1
            if not detected:
                continue
            totals["detected"] += 1
            lang["detected"] += 1
            per_cat[key]["detected"] += 1
            if lb.span is not None:
                totals["detected_with_span"] += 1
                lang["detected_with_span"] += 1
                if over:
                    totals["localized"] += 1
                    lang["localized"] += 1
            if cat_flags:
                totals["correct_category"] += 1
                lang["correct_category"] += 1
                per_cat[key]["correct_category"] += 1
        for f in flags:
            true = any(_true_flag(lb, f) for lb in labels)
            per_cat[f.category.value]["flags"] += 1
            per_cat[f.category.value]["true_flags"] += int(true)
            for check in f.checks:
                per_check[check]["flags"] += 1
                per_check[check]["true_flags"] += int(true)
            if f.checks == ["llm_judge"]:
                judge["judge_only_flags"] += 1
                judge["judge_only_true"] += int(true)
            elif f.judge and f.judge.startswith("confirmed"):
                judge["confirmed_flags"] += 1
                judge["confirmed_true"] += int(true)
        for f in r.flags + r.rejected:
            if f.judge and f.judge.startswith("rejected"):
                judge["rejected_candidates"] += 1
                judge["rejected_but_labeled"] += int(any(_true_flag(lb, f) for lb in labels))

    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    categories = {}
    for key in sorted(per_cat):
        c = per_cat[key]
        precision = ratio(c["true_flags"], c["flags"])
        recall = ratio(c["correct_category"], c["labels"])
        f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else (0.0 if c["labels"] or c["flags"] else None)
        categories[key] = {
            "labels": c["labels"],
            "flags": c["flags"],
            "precision": precision,
            "recall": recall,
            "detection_recall": ratio(c["detected"], c["labels"]),
            "f1": f1,
        }
    headline = {
        "detection_recall": ratio(totals["detected"], totals["labels"]),
        "false_alarm_rate": ratio(totals["false_alarms"], totals["clean"]),
        "localization": ratio(totals["localized"], totals["detected_with_span"]),
        "localization_recall": ratio(totals["localized"], totals["with_span"]),
        "category_accuracy": ratio(totals["correct_category"], totals["detected"]),
    }
    return {
        "labeled_items": len(items),
        "clean_items": totals["clean"],
        "seeded_errors": totals["labels"],
        "headline": headline,
        "counts": totals,
        "per_category": categories,
        "per_language": {
            k: {
                "labels": v["labels"],
                "clean": v["clean"],
                "detection_recall": ratio(v["detected"], v["labels"]),
                "false_alarm_rate": ratio(v["false_alarms"], v["clean"]),
                "localization": ratio(v["localized"], v["detected_with_span"]),
                "localization_recall": ratio(v["localized"], v["with_span"]),
                "category_accuracy": ratio(v["correct_category"], v["detected"]),
            }
            for k, v in sorted(per_lang.items())
        },
        "per_check": {k: {"flags": v["flags"], "precision": ratio(v["true_flags"], v["flags"])} for k, v in sorted(per_check.items())},
        "judge": {
            **judge,
            "judge_only_precision": ratio(judge["judge_only_true"], judge["judge_only_flags"]),
            "confirmed_precision": ratio(judge["confirmed_true"], judge["confirmed_flags"]),
            "rejection_error_rate": ratio(judge["rejected_but_labeled"], judge["rejected_candidates"]),
        },
    }


def _true_flag(label: LabelView, flag: Flag) -> bool:
    area = _label_area(label)
    return (area is None or flag.area == area) and category_match(label, flag) and (label.span is None or overlaps(label, flag))


def tune(results: list[ItemResult], base: Thresholds, labeled_ids: set[str], max_false_alarm: float = TARGETS["false_alarm_rate"]) -> tuple[Thresholds, dict]:
    """Per-category F1-optimal thresholds, then raise thresholds greedily until the false-alarm target holds."""
    items = [r for r in results if r.status == "scored" and r.id in labeled_ids]
    th = base.model_copy(deep=True)
    log: dict = {"per_category": {}, "false_alarm_steps": []}
    support = defaultdict(int)
    for r in items:
        for lb in _labels(r):
            if lb.category:
                support[lb.category.value] += 1
    for cat in Category:
        confs = sorted({f.confidence for r in items for f in r.flags + r.rejected if f.category == cat})
        if support[cat.value] < MIN_SUPPORT or not confs:
            log["per_category"][cat.value] = {"support": support[cat.value], "tuned": False, "threshold": th.threshold(cat)}
            continue
        grid = sorted({round((a + b) / 2, 4) for a, b in zip([0.0] + confs, confs + [1.0], strict=True)} | {th.threshold(cat)})
        best_t, best_f1 = th.threshold(cat), -1.0
        for t in grid:
            trial = th.model_copy(deep=True)
            trial.flag[cat.value] = t
            stats = evaluate(items, trial, labeled_ids)["per_category"].get(cat.value, {})
            f1 = stats.get("f1") or 0.0
            if f1 > best_f1 + 1e-9 or (abs(f1 - best_f1) <= 1e-9 and abs(t - 0.5) < abs(best_t - 0.5)):
                best_t, best_f1 = t, f1
        th.flag[cat.value] = max(best_t, 0.2)
        log["per_category"][cat.value] = {"support": support[cat.value], "tuned": True, "threshold": th.flag[cat.value], "f1": round(best_f1, 4)}

    for _ in range(60):
        metrics = evaluate(items, th, labeled_ids)
        fa = metrics["headline"]["false_alarm_rate"]
        if fa is None or fa <= max_false_alarm:
            break
        best_move = None
        for cat in Category:
            confs = sorted({f.confidence for r in items for f in r.flags + r.rejected if f.category == cat and f.confidence >= th.threshold(cat)})
            if not confs:
                continue
            trial = th.model_copy(deep=True)
            trial.flag[cat.value] = round(confs[0] + 1e-4, 4)
            m = evaluate(items, trial, labeled_ids)["headline"]
            gain = (fa - (m["false_alarm_rate"] or 0.0)) - 0.5 * ((metrics["headline"]["detection_recall"] or 0) - (m["detection_recall"] or 0))
            if best_move is None or gain > best_move[0]:
                best_move = (gain, cat.value, trial.flag[cat.value])
        if best_move is None or best_move[0] <= 0:
            break
        th.flag[best_move[1]] = best_move[2]
        log["false_alarm_steps"].append({"category": best_move[1], "threshold": best_move[2]})
    return th, log
