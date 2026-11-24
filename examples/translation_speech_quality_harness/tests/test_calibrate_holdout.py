import random

from bodhan_eval.calibrate import (
    leave_one_language_out,
    sweep_category_threshold,
)
from bodhan_eval.inject_holdout import build_injector_b_dataset, inject_holdout_text
from bodhan_eval.models import Item, Label


def test_injector_b_uses_distinct_seeded_mechanisms():
    item = Item(
        id="b",
        lang="hi",
        source="The water is at 100 degrees.",
        translation="पानी 100 kg है।",
    )
    first, first_label = inject_holdout_text(
        item,
        "number",
        random.Random(91),
    )
    second, second_label = inject_holdout_text(
        item,
        "number",
        random.Random(91),
    )
    assert first == second
    assert first_label.extra["method"] == "unit_symbol_swap"
    assert first_label.annotator == "injector"
    assert first != item.translation


def _items_for_sweep() -> tuple[list[Item], dict]:
    items = [
        Item(
            id=str(index),
            lang="hi",
            source=f"source-{index // 2}",
            translation=f"translation-{index}",
            labels=[],
        )
        for index in range(12)
    ]
    for index, item in enumerate(items):
        item.labels = (
            [Label("number", "major", (0, 1), "injector")]
            if index < 6
            else []
        )
    predictions = {
        (item.id, item.lang): (
            [{"category": "number", "confidence": 0.9 - (index * 0.1), "span": [0, 1]}]
            if index < 6
            else [{"category": "number", "confidence": 0.4, "span": [0, 1]}]
        )
        for index, item in enumerate(items)
    }
    return items, predictions


def test_threshold_sweep_requires_at_least_five_positives_and_negatives():
    result = sweep_category_threshold(
        [],
        {},
        "number",
    )
    assert result["status"] == "insufficient_support"
    assert result["threshold_origin"] == "defaults_not_tuned"

    items, predictions = _items_for_sweep()
    result = sweep_category_threshold(items, predictions, "number")
    assert result["status"] == "swept"
    assert result["threshold_origin"] == "swept"
    assert 0.0 <= result["threshold"] <= 1.0


def test_leave_one_language_out_is_separate_from_unseen():
    items = [
        Item(
            id=f"{lang}-{index}",
            lang=lang,
            source=f"{lang}-source-{index}",
            translation=f"translation-{index}",
            labels=(
                [Label("number", "major", (0, 1), "injector")]
                if index % 2 == 0
                else []
            ),
        )
        for lang in ("hi", "ta", "bn")
        for index in range(4)
    ]
    predictions = {
        (item.id, item.lang): (
            [{"category": "number", "confidence": 0.9, "span": [0, 1]}]
            if item.labels
            else []
        )
        for item in items
    }
    result = leave_one_language_out(items, predictions, seed=17)
    assert set(result["held_out_languages"]) == {"hi", "ta", "bn"}
    assert all(
        metrics["threshold_origin"] in {"defaults_not_tuned", "defaults"}
        for lang_metrics in result["languages"].values()
        for metrics in lang_metrics.values()
    )


def test_injector_b_dataset_is_seeded_and_mixed():
    clean = [
        Item(
            id=str(index),
            lang="hi",
            source=f"Source {index}",
            translation="पानी १०० किलो है।",
        )
        for index in range(20)
    ]
    first = build_injector_b_dataset(clean, seed=313, error_rate=0.5)
    second = build_injector_b_dataset(clean, seed=313, error_rate=0.5)
    assert [item.translation for item in first] == [
        item.translation for item in second
    ]
    assert all(
        label.extra.get("injector") == "B"
        for item in first
        for label in item.labels
    )
