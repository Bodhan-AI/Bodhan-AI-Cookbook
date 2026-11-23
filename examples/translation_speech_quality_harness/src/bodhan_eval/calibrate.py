import random
from dataclasses import dataclass
from math import sqrt
from typing import Any

from .config import DEFAULT_CALIBRATION_THRESHOLD
from .labels import canonical_category
from .models import Item, Label
from .spans import overlap

_CALIBRATION_CATEGORIES = (
    "omission",
    "addition",
    "mistranslation",
    "number",
    "term",
    "format",
    "language",
    "speech_omission",
    "repetition",
    "garbled",
)


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    if trials <= 0:
        return (0.0, 0.0)
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    margin = z * sqrt(proportion * (1 - proportion) / trials + z * z / (4 * trials * trials)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


@dataclass(frozen=True)
class Split:
    dev: list
    test: list


def split_by_source(items: list, seed: int) -> Split:
    rng = random.Random(seed)
    source_groups: dict[str, list] = {}
    for item in items:
        source_groups.setdefault(item.source, []).append(item)
    keys = list(source_groups)
    rng.shuffle(keys)
    cut = len(keys) // 2
    dev = [item for key in keys[:cut] for item in source_groups[key]]
    test = [item for key in keys[cut:] for item in source_groups[key]]
    return Split(dev=dev, test=test)


def bootstrap_mean_ci(
    values: list[float],
    seed: int,
    samples: int = 1000,
) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    if samples < 1:
        raise ValueError("samples must be positive")
    rng = random.Random(seed)
    means = sorted(
        sum(rng.choice(values) for _ in values) / len(values)
        for _ in range(samples)
    )
    lower = means[int((samples - 1) * 0.025)]
    upper = means[int((samples - 1) * 0.975)]
    return (round(lower, 6), round(upper, 6))


def _span(value: Any) -> tuple[float, float] | None:
    if isinstance(value, tuple | list) and len(value) == 2:
        start, end = value
        if isinstance(start, int | float) and isinstance(end, int | float):
            return (float(start), float(end))
    return None


def _predicted_spans(predicted: dict[str, Any], span_ref: str) -> list[tuple[float, float]]:
    """Spans of a prediction that a label can be compared with, per ``--span-ref``."""
    spans: list[tuple[float, float] | None] = []
    if span_ref in {"auto", "translation"}:
        spans.append(_span(predicted.get("span")))
    if span_ref in {"auto", "source"}:
        spans.append(_span(predicted.get("source_span")))
    return [span for span in spans if span is not None]


def _best_overlap(
    gold_span: tuple[float, float],
    predicted: dict[str, Any],
    span_ref: str,
) -> float | None:
    candidates = _predicted_spans(predicted, span_ref)
    if not candidates:
        return None
    return max(overlap(gold_span, span, mode="iou") for span in candidates)


def _matches(
    gold: Label,
    predicted: dict[str, Any],
    span_ref: str = "auto",
) -> tuple[bool, float | None]:
    if gold.type != predicted.get("category"):
        return False, None
    gold_span = _span(gold.span)
    if gold_span is None:
        return True, None
    span_overlap = _best_overlap(gold_span, predicted, span_ref)
    if span_overlap is None:
        return True, None
    return span_overlap >= 0.3, span_overlap


def _metric_record(
    labels: list[Label],
    predictions: list[dict[str, Any]],
    item_count: int,
    positive_items: int,
    span_ref: str = "auto",
) -> dict[str, Any]:
    unmatched_gold = set(range(len(labels)))
    true_positives = 0
    span_overlaps: list[float] = []
    for prediction in predictions:
        candidates: list[tuple[int, float | None]] = []
        for index in unmatched_gold:
            matched, span_overlap = _matches(labels[index], prediction, span_ref)
            if matched:
                candidates.append((index, span_overlap))
        if candidates:
            index, span_overlap = max(
                candidates,
                key=lambda candidate: (
                    candidate[1] if candidate[1] is not None else -1.0,
                    -candidate[0],
                ),
            )
            unmatched_gold.remove(index)
            true_positives += 1
            if span_overlap is not None:
                span_overlaps.append(span_overlap)
    predicted_count = len(predictions)
    gold_count = len(labels)
    false_positives = predicted_count - true_positives
    false_negatives = gold_count - true_positives
    return {
        "support_positives": gold_count,
        "support_negatives": max(0, item_count - positive_items),
        "predicted": predicted_count,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(
            true_positives / predicted_count if predicted_count else 0.0, 6
        ),
        "precision_ci95": list(wilson_interval(true_positives, predicted_count)),
        "recall": round(
            true_positives / gold_count if gold_count else 0.0, 6
        ),
        "recall_ci95": list(wilson_interval(true_positives, gold_count)),
        "span_overlap_mean": round(
            sum(span_overlaps) / len(span_overlaps) if span_overlaps else 0.0, 6
        ),
        "span_support": len(span_overlaps),
        "insufficient_support": gold_count == 0,
    }


def _category_accuracy(
    labels: list[Label],
    predictions: list[dict[str, Any]],
    span_ref: str = "auto",
) -> dict[str, Any]:
    unmatched_gold = set(range(len(labels)))
    correct = 0
    support = 0
    for prediction in predictions:
        if not _predicted_spans(prediction, span_ref):
            continue
        candidates: list[tuple[int, float]] = []
        for index in unmatched_gold:
            label_span = _span(labels[index].span)
            if label_span is None:
                continue
            span_overlap = _best_overlap(label_span, prediction, span_ref)
            if span_overlap is not None and span_overlap >= 0.3:
                candidates.append((index, span_overlap))
        if not candidates:
            continue
        index, _ = max(candidates, key=lambda candidate: (candidate[1], -candidate[0]))
        unmatched_gold.remove(index)
        support += 1
        correct += labels[index].type == prediction.get("category")
    return {
        "accuracy": round(correct / support if support else 0.0, 6),
        "ci95": list(wilson_interval(correct, support)),
        "correct": correct,
        "support": support,
    }


def _category_set(items: list[Item], predictions: dict) -> set[str]:
    categories = set(_CALIBRATION_CATEGORIES)
    categories.update(
        label.type
        for item in items
        for label in item.labels
        if label.annotator in {None, "injector", "self"}
    )
    categories.update(
        category
        for item in items
        for prediction in predictions.get((item.id, item.lang), [])
        if (category := canonical_category(
            str(prediction.get("category", "")),
            str(prediction.get("modality", "text")),
        )) is not None
    )
    return categories


def _thresholds_for(
    dev_items: list[Item],
    all_items: list[Item],
    predictions: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    return {
        category: sweep_category_threshold(dev_items, predictions, category)
        for category in sorted(_category_set(all_items, predictions))
    }


def _apply_thresholds(
    items: list[Item],
    predictions: dict[tuple[str, str], list[dict[str, Any]]],
    thresholds: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    filtered: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        item_key = (item.id, item.lang)
        filtered[item_key] = []
        for prediction in predictions.get(item_key, []):
            if prediction.get("suppressed_reason"):
                continue
            category = canonical_category(
                str(prediction.get("category", "")),
                str(prediction.get("modality", "text")),
            )
            if category is None:
                continue
            threshold = thresholds.get(category, {}).get(
                "threshold",
                DEFAULT_CALIBRATION_THRESHOLD,
            )
            confidence = float(
                prediction.get("confidence", DEFAULT_CALIBRATION_THRESHOLD)
            )
            if confidence >= threshold:
                filtered[item_key].append(prediction)
    return filtered


def _annotator_metrics(
    items: list[Item],
    predictions: dict[tuple[str, str], list[dict[str, Any]]],
    annotator: str,
    span_ref: str = "auto",
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for lang in sorted({item.lang for item in items}):
        lang_items = [item for item in items if item.lang == lang]
        labels_by_category: dict[str, list[Label]] = {}
        label_items_by_category: dict[str, set[str]] = {}
        predictions_by_category: dict[str, list[dict[str, Any]]] = {}
        all_labels: list[Label] = []
        all_predictions: list[dict[str, Any]] = []
        for item in lang_items:
            item_labels = [
                label for label in item.labels
                if (label.annotator or "unspecified") == annotator
            ]
            all_labels.extend(item_labels)
            for label in item_labels:
                labels_by_category.setdefault(label.type, []).append(label)
                label_items_by_category.setdefault(label.type, set()).add(item.id)
            item_predictions = predictions.get((item.id, item.lang), [])
            all_predictions.extend(item_predictions)
            for prediction in item_predictions:
                category = str(prediction.get("category", "unmapped"))
                predictions_by_category.setdefault(category, []).append(prediction)
        per_category: dict[str, Any] = {}
        categories = set(labels_by_category) | set(predictions_by_category)
        for category in sorted(categories):
            per_category[category] = _metric_record(
                labels_by_category.get(category, []),
                predictions_by_category.get(category, []),
                len(lang_items),
                len(label_items_by_category.get(category, set())),
                span_ref,
            )
        clean_items = [
            item for item in lang_items
            if not any(
                (label.annotator or "unspecified") == annotator
                for label in item.labels
            )
        ]
        false_alarm_items = sum(
            bool(predictions.get((item.id, item.lang), []))
            for item in clean_items
        )
        result[lang] = {
            "items": len(lang_items),
            "per_category": per_category,
            "category_accuracy": _category_accuracy(all_labels, all_predictions, span_ref),
            "clean_items": len(clean_items),
            "false_alarm_items": false_alarm_items,
            "false_alarm_rate": round(
                false_alarm_items / len(clean_items) if clean_items else 0.0,
                6,
            ),
            "false_alarm_ci95": list(
                wilson_interval(false_alarm_items, len(clean_items))
            ),
        }
    return result


def sweep_category_threshold(
    items: list[Item],
    predictions: dict[tuple[str, str], list[dict[str, Any]]],
    category: str,
) -> dict[str, Any]:
    positives = sum(
        any(label.type == category for label in item.labels)
        for item in items
    )
    negatives = len(items) - positives
    if positives < 5 or negatives < 5:
        return {
            "status": "insufficient_support",
            "threshold_origin": "defaults_not_tuned",
            "threshold": DEFAULT_CALIBRATION_THRESHOLD,
            "support_positives": positives,
            "support_negatives": negatives,
        }

    confidences = sorted({
        float(prediction.get("confidence", DEFAULT_CALIBRATION_THRESHOLD))
        for item in items
        for prediction in predictions.get((item.id, item.lang), [])
        if not prediction.get("suppressed_reason")
        and canonical_category(
            str(prediction.get("category", "")),
            str(prediction.get("modality", "text")),
        ) == category
    })
    candidates = sorted({0.0, 1.0, *confidences})
    best = (float("-inf"), DEFAULT_CALIBRATION_THRESHOLD)
    for threshold in candidates:
        tp = fp = fn = 0
        for item in items:
            gold = any(label.type == category for label in item.labels)
            predicted = any(
                not prediction.get("suppressed_reason")
                and canonical_category(
                    str(prediction.get("category", "")),
                    str(prediction.get("modality", "text")),
                ) == category
                and float(
                    prediction.get(
                        "confidence",
                        DEFAULT_CALIBRATION_THRESHOLD,
                    )
                ) >= threshold
                for prediction in predictions.get((item.id, item.lang), [])
            )
            if gold and predicted:
                tp += 1
            elif gold:
                fn += 1
            elif predicted:
                fp += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f05 = (
            1.25 * precision * recall / (0.25 * precision + recall)
            if precision or recall
            else 0.0
        )
        if f05 > best[0] or (f05 == best[0] and threshold > best[1]):
            best = (f05, threshold)
    return {
        "status": "swept",
        "threshold_origin": "swept",
        "threshold": round(best[1], 6),
        "f0_5": round(best[0], 6),
        "support_positives": positives,
        "support_negatives": negatives,
    }


def leave_one_language_out(
    labeled_items: list[Item],
    predictions: dict[tuple[str, str], list[dict[str, Any]]],
    seed: int,
    span_ref: str = "auto",
) -> dict[str, Any]:
    languages = sorted({item.lang for item in labeled_items})
    results: dict[str, Any] = {}
    annotators = sorted({
        label.annotator or "unspecified"
        for item in labeled_items
        for label in item.labels
    }) or ["unspecified"]
    for held_out in languages:
        dev_items = [item for item in labeled_items if item.lang != held_out]
        test_items = [item for item in labeled_items if item.lang == held_out]
        threshold_by_category = _thresholds_for(
            dev_items,
            dev_items,
            predictions,
        )
        test_predictions = _apply_thresholds(
            test_items,
            predictions,
            threshold_by_category,
        )
        lang_metrics: dict[str, Any] = {}
        for annotator in annotators:
            gold_count = sum(
                1
                for item in test_items
                for label in item.labels
                if (label.annotator or "unspecified") == annotator
            )
            predictions_count = sum(
                len(test_predictions.get((item.id, item.lang), []))
                for item in test_items
            )
            metrics = _annotator_metrics(
                test_items,
                test_predictions,
                annotator,
                span_ref,
            ).get(held_out, {})
            lang_metrics[annotator] = {
                "items": len(test_items),
                "gold_labels": gold_count,
                "predictions": predictions_count,
                "thresholds": threshold_by_category,
                "metrics": metrics,
                "threshold_origin": (
                    "swept"
                    if any(
                        value.get("threshold_origin") == "swept"
                        for value in threshold_by_category.values()
                    )
                    else "defaults_not_tuned"
                ),
            }
        results[held_out] = lang_metrics
    return {
        "seed": seed,
        "held_out_languages": languages,
        "languages": results,
    }


def evaluate_labeled_items(
    labeled_items: list[Item],
    predictions: dict[tuple[str, str], list[dict[str, Any]]],
    seed: int,
    span_ref: str = "auto",
) -> dict[str, Any]:
    split = split_by_source(labeled_items, seed)
    test_items = split.test
    thresholds = _thresholds_for(split.dev, split.dev, predictions)
    threshold_status = (
        "swept"
        if any(
            value.get("threshold_origin") == "swept"
            for value in thresholds.values()
        )
        else "insufficient_support"
    )
    threshold_origin = (
        "swept"
        if threshold_status == "swept"
        else "defaults_not_tuned"
    )
    annotators = sorted({
        label.annotator or "unspecified"
        for item in labeled_items
        for label in item.labels
    })
    if not annotators:
        return {
            "status": "insufficient_support",
            "reason": "no_labels",
            "seed": seed,
            "support": {
                "dev_sources": len({item.source for item in split.dev}),
                "test_sources": len({item.source for item in split.test}),
                "test_items": len(test_items),
            },
            "metrics": {},
            "threshold_status": threshold_status,
            "threshold_origin": threshold_origin,
            "thresholds": thresholds,
        }

    test_predictions = _apply_thresholds(test_items, predictions, thresholds)
    metrics = {
        annotator: _annotator_metrics(test_items, test_predictions, annotator, span_ref)
        for annotator in annotators
    }
    test_gold_count = sum(
        1
        for item in test_items
        for label in item.labels
        if (label.annotator or "unspecified") in annotators
    )

    return {
        "status": "ok" if test_gold_count else "insufficient_support",
        "reason": None if test_gold_count else "no_test_labels",
        "seed": seed,
        "support": {
            "dev_sources": len({item.source for item in split.dev}),
            "test_sources": len({item.source for item in split.test}),
            "test_items": len(test_items),
            "test_labels": test_gold_count,
        },
        "metrics": metrics,
        "threshold_status": threshold_status,
        "threshold_origin": threshold_origin,
        "thresholds": thresholds,
    }
