"""Calibrate detectors against translations with known, injected errors.

Each calibration case is a clean translation plus a copy with exactly one
labelled error (or none: a *clean case*, used to measure false alarms).
Three detector modes are scored separately:

``reference_based``
    The clean translation is used as the reference and the deterministic
    diff checks compare it with the mutated text. This is an upper bound:
    in production you rarely have the clean translation.
``reference_free``
    Only the English source and the mutated translation are used (the
    production situation without references). Requires ``source_text``.
``llm_judge``
    The LLM judge sees the source and the mutated translation. Scored in the
    judge's coarser taxonomy. Requires ``source_text`` and ``--llm-judge``.

Synthetic errors are easier than real ones (one clean, local edit), so these
numbers are an optimistic sanity check, not an estimate of real accuracy.
"""

from __future__ import annotations

import random
from collections import defaultdict

from bodhan_eval.checks import (
    check_against_reference,
    check_formatting,
    check_markdown_structure,
    check_numbers,
    has_markdown,
    run_deterministic_checks,
)

# Detector category -> calibration label.
DETECTOR_TO_LABEL = {
    "reference_mismatch": "mistranslation",
    "markdown_structure": "formatting_mismatch",
    "latex_integrity": "formatting_mismatch",
    "date_time_mismatch": "number_mismatch",
    "terminology_mismatch": "entity_mismatch",
}

# Calibration label -> LLM judge category (coarser taxonomy).
LABEL_TO_JUDGE = {
    "number_mismatch": "number_unit",
    "unit_mismatch": "number_unit",
    "entity_mismatch": "term_entity",
    "formatting_mismatch": "format",
    "repetition": "addition",
    "omission": "omission",
    "addition": "addition",
    "mistranslation": "mistranslation",
}


def calculate_calibration(cases: list[dict]) -> dict:
    """
    Each case:
    {
        "id": "case-1",
        "expected_categories": ["number_mismatch"],
        "predicted_categories": ["number_mismatch"]
    }

    An empty expected_categories list represents a clean case.
    """
    categories = defaultdict(lambda: {
        "tp": 0,
        "fp": 0,
        "fn": 0,
    })

    clean_cases = 0
    clean_false_alarms = 0

    for case in cases:
        expected = set(case.get("expected_categories", []))
        predicted = set(case.get("predicted_categories", []))

        if not expected:
            clean_cases += 1
            if predicted:
                clean_false_alarms += 1

        for category in expected | predicted:
            if category in expected and category in predicted:
                categories[category]["tp"] += 1
            elif category in predicted:
                categories[category]["fp"] += 1
            elif category in expected:
                categories[category]["fn"] += 1

    results = {}
    total_tp = total_fp = total_fn = 0

    for category, counts in sorted(categories.items()):
        tp = counts["tp"]
        fp = counts["fp"]
        fn = counts["fn"]

        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall else 0.0
        )

        results[category] = {
            **counts,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

        total_tp += tp
        total_fp += fp
        total_fn += fn

    precision = (
        total_tp / (total_tp + total_fp)
        if total_tp + total_fp else 0.0
    )
    recall = (
        total_tp / (total_tp + total_fn)
        if total_tp + total_fn else 0.0
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )

    return {
        "categories": results,
        "overall": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "cases": len(cases),
            "clean_cases": clean_cases,
            "clean_false_alarms": clean_false_alarms,
            "clean_false_alarm_rate": (
                round(clean_false_alarms / clean_cases, 4) if clean_cases else None
            ),
        },
    }


def _failed_labels(checks: list[dict]) -> list[str]:
    return sorted({
        DETECTOR_TO_LABEL.get(check["category"], check["category"])
        for check in checks
        if check.get("status") == "fail"
        or ("status" not in check and check.get("passed") is False)
    })


def reference_based_checks(clean: str, mutated: str, language: str | None = None) -> list[dict]:
    checks = [
        check_numbers(clean, mutated, language),
        check_formatting(clean, mutated),
        *check_against_reference(clean, mutated, language),
    ]
    if has_markdown(clean):
        checks.append(check_markdown_structure(clean, mutated))
    return checks


def evaluate_injected_cases(cases: list[dict], judge=None) -> dict:
    """
    Each case contains:
    {
        "id": "case-1",
        "clean_translation": "...",
        "mutated_text": "...",
        "injected_category": "number_mismatch",   # "" for a clean case
        "source_text": "...",                     # optional, enables
        "language": "hi"                          # reference-free + LLM modes
    }

    ``metrics`` (top level) holds the reference-based scores for backwards
    compatibility; ``modes`` holds every mode that could be run.
    """
    modes: dict[str, list[dict]] = {"reference_based": [], "reference_free": [], "llm_judge": []}
    item_results = []
    judge_status = defaultdict(int)

    for case in cases:
        clean = case["clean_translation"]
        mutated = case["mutated_text"]
        language = case.get("language")
        source = case.get("source_text")

        expected = (
            [case["injected_category"]]
            if case.get("injected_category")
            else []
        )

        checks = reference_based_checks(clean, mutated, language)
        predicted = _failed_labels(checks)
        modes["reference_based"].append({
            "id": case["id"], "expected_categories": expected, "predicted_categories": predicted,
        })

        item = {
            "id": case["id"],
            "language": language,
            "expected": expected,
            "predicted": predicted,
            "checks": checks,
        }

        if source:
            free_checks = run_deterministic_checks({
                "source_text": source,
                "translation": mutated,
                "language": language,
            })
            free_predicted = _failed_labels(free_checks)
            modes["reference_free"].append({
                "id": case["id"], "expected_categories": expected,
                "predicted_categories": free_predicted,
            })
            item["reference_free_predicted"] = free_predicted

            if judge is not None:
                outcome = judge.judge(source, mutated, language or "")
                judge_status[outcome.status] += 1
                item["llm_judge"] = outcome.to_dict()
                if outcome.status == "ok":
                    modes["llm_judge"].append({
                        "id": case["id"],
                        "expected_categories": [LABEL_TO_JUDGE.get(e, e) for e in expected],
                        "predicted_categories": sorted({
                            f.category.value for f in outcome.findings
                        }),
                    })

        item_results.append(item)

    scored = {
        mode: calculate_calibration(mode_cases) if mode_cases else None
        for mode, mode_cases in modes.items()
    }

    result = {
        "metrics": scored["reference_based"] or calculate_calibration([]),
        "modes": scored,
        "items": item_results,
    }
    if judge is not None:
        result["llm_judge_status"] = dict(judge_status)
    return result


def build_calibration_cases(
    records: list[dict],
    categories: list[str] | None = None,
    per_category: int = 6,
    clean_cases: int = 6,
    seed: int = 13,
) -> list[dict]:
    """Inject labelled errors into dataset translations.

    Records are visited in a seeded shuffle and round-robin across
    languages, so each category gets a language mix. The output is fully
    deterministic for a given (records, categories, per_category, seed).
    """
    from bodhan_eval.injector import SUPPORTED_CATEGORIES, inject_labelled_error

    categories = categories or SUPPORTED_CATEGORIES
    usable = [r for r in records if r.get("translation")]

    by_language = defaultdict(list)
    for record in usable:
        by_language[record["language"]].append(record)

    rng = random.Random(seed)
    for language_records in by_language.values():
        rng.shuffle(language_records)

    def round_robin():
        queues = [list(v) for _, v in sorted(by_language.items())]
        while any(queues):
            for queue in queues:
                if queue:
                    yield queue.pop(0)

    cases = []

    for category in categories:
        made = 0
        for record in round_robin():
            if made >= per_category:
                break
            injected = inject_labelled_error(
                record["translation"], category, record["language"], seed=seed + made
            )
            if injected is None or injected["mutated_text"] == record["translation"]:
                continue
            cases.append({
                "id": f"cal-{category}-{record['id']}",
                "language": record["language"],
                "source_text": record["source_text"],
                "clean_translation": record["translation"],
                "mutated_text": injected["mutated_text"],
                "injected_category": injected["injected_category"],
                "injection": {
                    "span": injected["span"],
                    "original": injected["original"],
                    "replacement": injected["replacement"],
                    "seed": injected["seed"],
                },
            })
            made += 1

    for index, record in enumerate(round_robin()):
        if index >= clean_cases:
            break
        cases.append({
            "id": f"cal-clean-{record['id']}",
            "language": record["language"],
            "source_text": record["source_text"],
            "clean_translation": record["translation"],
            "mutated_text": record["translation"],
            "injected_category": "",
        })

    return cases
