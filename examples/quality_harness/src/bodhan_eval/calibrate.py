"""Calibration against human/injector labels.

Reports precision, recall, F1, localization overlap and category accuracy per category and per
language, then fits a few key thresholds by grid search -- maximizing F1 under a false-alarm
constraint (<=10% of clean items) -- WITHOUT re-calling any model: it re-runs only the cheap
combiner over the already-collected evidence. Categories/languages with too few labels fall
back to pooled/default values and are marked 'low support'.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from .align import overlap_fraction_of, spans_overlap
from .combiner import combine, finalize_item
from .config import Thresholds
from .models import Item, ItemResult, Label, Span

MIN_SUPPORT = 5
LOCALIZATION_MIN = 0.7


@dataclass
class ItemEvidence:
    """Everything calibration needs about one item: its labels and its collected evidence."""

    item: Item
    result: ItemResult


def _label_spans(label: Label, item: Item) -> list[tuple[str, Span | None]]:
    """Return the label's declared coordinate space and span, when supplied."""
    if label.span is None:
        return [("source", None)]
    primary = label.span_space
    return [(primary, label.span)]


def _flag_span(flag, space: str) -> Span | None:
    return flag.source_span if space == "source" else flag.target_span


def _match(label: Label, item: Item, flags) -> tuple[object, float, bool] | None:
    """Find the flag best matching a label by span overlap in its declared coordinate space.
    Returns (flag, localization_fraction, category_correct) or None."""
    best = None
    for space, span in _label_spans(label, item):
        for fl in flags:
            fspan = _flag_span(fl, space)
            if span is None:
                # no span given: match on category presence anywhere
                if fl.category == label.type:
                    return (fl, 1.0, True)
                continue
            if fspan and spans_overlap(span, fspan):
                frac = overlap_fraction_of(span, fspan)
                cat_ok = fl.category == label.type
                cand = (fl, frac, cat_ok)
                if best is None or (cat_ok, frac) > (best[2], best[1]):
                    best = cand
    return best


def evaluate(pairs: list[ItemEvidence]) -> dict:
    """Compute detection metrics overall, per language and per category."""
    agg = _Accumulator()
    for pe in pairs:
        item, res = pe.item, pe.result
        labels = [lab for lab in item.labels]
        flags = res.flags
        # A missing labels field means unknown, not a reviewed clean example.
        if not labels and "labels" not in item.raw:
            continue
        if not labels:
            # clean item: any flag is a false alarm
            agg.clean(item.lang, flagged=bool(flags))
            for fl in flags:
                agg.predicted(item.lang, fl.category, correct=False)
            continue
        matched_flags = set()
        used_flags = set()
        for lab in labels:
            m = _match(lab, item, [fl for fl in flags if id(fl) not in used_flags])
            detected = m is not None
            loc_ok = bool(m and m[1] >= LOCALIZATION_MIN)
            cat_ok = bool(m and m[2])
            if m:
                used_flags.add(id(m[0]))
                if cat_ok:
                    matched_flags.add(id(m[0]))
            agg.labeled(item.lang, lab.type, detected=detected, loc_ok=loc_ok, cat_ok=cat_ok,
                        has_span=lab.span is not None)
        # flags on a labeled item that matched nothing count toward precision denominator
        for fl in flags:
            agg.predicted(item.lang, fl.category, correct=id(fl) in matched_flags)
    return agg.finalize()


class _Accumulator:
    def __init__(self):
        self.data: dict = {}
        self.clean_total: dict[str, int] = {}
        self.clean_flagged: dict[str, int] = {}

    def _cell(self, lang: str, cat: str) -> dict:
        return self.data.setdefault((lang, cat), {
            "labels": 0, "detected": 0, "matches": 0, "loc_ok": 0, "loc_total": 0, "cat_ok": 0,
            "predicted": 0, "predicted_correct": 0})

    def labeled(self, lang, cat, detected, loc_ok, cat_ok, has_span):
        for key in ((lang, cat), ("_all", cat), (lang, "_all"), ("_all", "_all")):
            c = self._cell(*key)
            c["labels"] += 1
            c["detected"] += int(cat_ok if key[1] != "_all" else detected)
            c["matches"] += int(detected)
            c["loc_ok"] += int(loc_ok and has_span)
            c["loc_total"] += int(detected and has_span)
            c["cat_ok"] += int(cat_ok)

    def predicted(self, lang, cat, correct):
        for key in ((lang, cat), ("_all", cat), (lang, "_all"), ("_all", "_all")):
            c = self._cell(*key)
            c["predicted"] += 1
            c["predicted_correct"] += int(correct)

    def clean(self, lang, flagged):
        for L in (lang, "_all"):
            self.clean_total[L] = self.clean_total.get(L, 0) + 1
            self.clean_flagged[L] = self.clean_flagged.get(L, 0) + int(flagged)

    def finalize(self) -> dict:
        out = {"cells": {}, "false_alarms": {}}
        for (lang, cat), c in self.data.items():
            labels = c["labels"]
            pred = c["predicted"]
            recall = c["detected"] / labels if labels else None
            precision = c["predicted_correct"] / pred if pred else None
            f1 = (2 * precision * recall / (precision + recall)
                  if precision is not None and recall is not None and precision + recall
                  else (0.0 if precision is not None and recall is not None else None))
            out["cells"][f"{lang}|{cat}"] = {
                "lang": lang, "category": cat, "labels": labels, "predictions": pred,
                "recall": _r(recall), "precision": _r(precision), "f1": _r(f1),
                "localization": _r(c["loc_ok"] / c["loc_total"] if c["loc_total"] else None),
                "category_accuracy": _r(c["cat_ok"] / c["matches"] if c["matches"] else None),
                "low_support": labels < MIN_SUPPORT,
            }
        for lang, total in self.clean_total.items():
            flagged = self.clean_flagged.get(lang, 0)
            out["false_alarms"][lang] = {"clean_items": total, "flagged": flagged,
                                         "rate": _r(flagged / total if total else None)}
        return out


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


# ---- threshold fitting ----------------------------------------------------------------

_GRID = {
    "judge_confidence_cut": [0.3, 0.4, 0.5, 0.6, 0.7],
}


def fit_thresholds(pairs: list[ItemEvidence], base: Thresholds) -> tuple[Thresholds, dict]:
    """Greedy per-key grid search over the cheap combiner. Keeps a key's new value only if it
    improves overall F1 without pushing the clean-item false-alarm rate above 10%."""
    fitted = copy.deepcopy(base)
    notes: dict = {"searched": {}, "false_alarm_constraint": 0.10}

    def score(th: Thresholds) -> tuple[float, float]:
        recombined = _recombine(pairs, th)
        metrics = evaluate(recombined)
        cell = metrics["cells"].get("_all|_all", {})
        fa = metrics["false_alarms"].get("_all", {}).get("rate")
        return (cell.get("f1") or 0.0, fa if fa is not None else 1.0)

    best_f1, best_fa = score(fitted)
    for key, values in _GRID.items():
        tried = []
        for v in values:
            cand = copy.deepcopy(fitted)
            cand.defaults[key] = float(v)
            f1, fa = score(cand)
            tried.append({"value": v, "f1": f1, "false_alarm": fa})
            if fa <= notes["false_alarm_constraint"] and f1 > best_f1:
                fitted, best_f1, best_fa = cand, f1, fa
        notes["searched"][key] = {"chosen": fitted.defaults[key], "trials": tried}
    notes["final_f1"] = best_f1
    notes["final_false_alarm"] = best_fa
    return fitted, notes


def _recombine(pairs: list[ItemEvidence], th: Thresholds) -> list[ItemEvidence]:
    out = []
    for pe in pairs:
        res = copy.copy(pe.result)
        res.flags = []
        verdict = pe.result.details.get("judge_verdict")
        outcome = combine(pe.result.evidence, th, pe.item.lang, verdict)
        res = finalize_item(_blank_like(pe.result), outcome)
        out.append(ItemEvidence(item=pe.item, result=res))
    return out


def _blank_like(res: ItemResult) -> ItemResult:
    new = ItemResult(id=res.id, lang=res.lang)
    new.evidence = res.evidence
    new.details = dict(res.details)
    return new


def thresholds_to_json(th: Thresholds, low_support: list[str]) -> dict:
    return {
        "defaults": th.defaults,
        "per_language": th.per_language,
        "judge_confidence_multiplier": th.judge_confidence_multiplier,
        "low_support": low_support,
    }
