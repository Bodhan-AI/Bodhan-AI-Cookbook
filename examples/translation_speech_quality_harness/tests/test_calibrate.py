from bodhan_eval.calibrate import evaluate_labeled_items, split_by_source
from bodhan_eval.models import Item, Label


def _calibration_items(count: int = 12) -> list[Item]:
    return [
        Item(
            id=f"item-{index}",
            lang="hi" if index % 2 else "ta",
            source=f"source group {index // 2}",
            translation=f"translation {index}",
            labels=(
                [Label("number", "major", (0, 1), annotator="injector")]
                if index % 3 == 0
                else []
            ),
        )
        for index in range(count)
    ]


def _predictions(items: list[Item]) -> dict[tuple[str, str], list[dict]]:
    return {
        (item.id, item.lang): (
            [{"category": "number", "severity": "major", "span": [0, 1]}]
            if item.labels
            else []
        )
        for item in items
    }


def test_source_split_is_deterministic_and_disjoint():
    items = _calibration_items()
    first = split_by_source(items, seed=41)
    second = split_by_source(items, seed=41)
    dev_sources = {item.source for item in first.dev}
    test_sources = {item.source for item in first.test}

    assert dev_sources.isdisjoint(test_sources)
    assert [item.id for item in first.dev] == [item.id for item in second.dev]
    assert [item.id for item in first.test] == [item.id for item in second.test]


def test_test_labels_do_not_leak_into_threshold_sweep():
    items = _calibration_items()
    predictions = _predictions(items)
    split = split_by_source(items, seed=7)

    before = evaluate_labeled_items(items, predictions, seed=7)

    for item in split.test:
        item.labels = [Label("format", "critical", (0, 1), annotator="self")]
        predictions[(item.id, item.lang)] = [
            {"category": "format", "severity": "critical", "span": [0, 1]}
        ]
    after = evaluate_labeled_items(items, predictions, seed=7)

    assert before["thresholds"] == after["thresholds"]
    assert before["metrics"] != after["metrics"]
    assert before["support"]["test_items"] == after["support"]["test_items"]
    assert before["status"] == "ok"


def test_swept_threshold_is_applied_to_held_out_predictions():
    items = [
        Item(
            id=f"threshold-{index}",
            lang="hi",
            source=f"independent source {index}",
            translation=f"translation {index}",
            labels=(
                [Label("number", "major", (0, 1), annotator="self")]
                if index % 2 == 0
                else []
            ),
        )
        for index in range(40)
    ]
    predictions = {
        (item.id, item.lang): [{
            "category": "number",
            "severity": "major",
            "span": [0, 1],
            "confidence": 0.95 if item.labels else 0.55,
        }]
        for item in items
    }

    result = evaluate_labeled_items(items, predictions, seed=3)
    metrics = result["metrics"]["self"]["hi"]

    assert result["thresholds"]["number"]["status"] == "swept"
    assert result["thresholds"]["number"]["threshold"] == 0.95
    assert metrics["per_category"]["number"]["false_positives"] == 0
    assert metrics["false_alarm_items"] == 0


def test_category_accuracy_counts_locatable_wrong_category_predictions():
    items = [
        Item(
            id=f"category-{index}",
            lang="hi",
            source=f"independent source {index}",
            translation=f"translation {index}",
            labels=[Label("number", "major", (0, 1), annotator="self")],
        )
        for index in range(20)
    ]
    predictions = {
        (item.id, item.lang): [{
            "category": "format",
            "severity": "major",
            "span": [0, 1],
            "confidence": 0.9,
        }]
        for item in items
    }

    result = evaluate_labeled_items(items, predictions, seed=7)
    metrics = result["metrics"]["self"]["hi"]["category_accuracy"]

    assert metrics["support"] > 0
    assert metrics["accuracy"] == 0.0
