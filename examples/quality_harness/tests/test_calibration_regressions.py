from bodhan_eval.calibrate import ItemEvidence, evaluate
from bodhan_eval.models import Flag, Item, ItemResult, Label


def test_precision_includes_clean_false_positives_and_ignores_unknown():
    error = Item(id="e", source="abc", lang="hi", labels=[Label("number_unit", span=(0, 2))],
                 raw={"labels": [{}]})
    clean = Item(id="c", source="abc", lang="hi", raw={"labels": []})
    unknown = Item(id="u", source="abc", lang="hi")
    flag = Flag("number_unit", "major", 1.0, source_span=(0, 2))
    metrics = evaluate([ItemEvidence(error, ItemResult("e", "hi", flags=[flag])),
                        ItemEvidence(clean, ItemResult("c", "hi", flags=[flag])),
                        ItemEvidence(unknown, ItemResult("u", "hi", flags=[flag]))])
    assert metrics["cells"]["_all|number_unit"]["precision"] == 0.5
    assert metrics["false_alarms"]["_all"]["clean_items"] == 1


def test_wrong_category_and_wrong_coordinate_do_not_inflate_precision():
    item = Item(id="e", source="abcd", lang="hi",
                labels=[Label("number_unit", span=(0, 2), span_space="target")],
                raw={"labels": [{}]})
    wrong_space = Flag("number_unit", "major", 1.0, source_span=(0, 2))
    metrics = evaluate([ItemEvidence(item, ItemResult("e", "hi", flags=[wrong_space]))])
    assert metrics["cells"]["_all|number_unit"]["recall"] == 0.0
    wrong_category = Flag("format", "major", 1.0, target_span=(0, 2))
    metrics = evaluate([ItemEvidence(item, ItemResult("e", "hi", flags=[wrong_category]))])
    assert metrics["cells"]["_all|format"]["precision"] == 0.0


def test_budget_reservations_prevent_overcommit():
    from bodhan_eval.cost import BudgetExceeded, CostLedger

    ledger = CostLedger(max_inr=1.0)
    ledger.reserve(0.7)
    try:
        ledger.reserve(0.4)
    except BudgetExceeded:
        pass
    else:
        raise AssertionError("reservation exceeded budget")
    ledger.settle("model", 0.7, 0.5)
    ledger.reserve(0.5)
    assert ledger.total == 0.5


def test_no_span_is_not_scored_as_localized():
    item = Item(id="x", source="abc", lang="hi", labels=[Label("format")], raw={"labels": [{}]})
    flag = Flag("format", "major", 1.0)
    metrics = evaluate([ItemEvidence(item, ItemResult("x", "hi", flags=[flag]))])
    assert metrics["cells"]["_all|_all"]["recall"] == 1.0
    assert metrics["cells"]["_all|_all"]["localization"] is None


def test_external_calibration_tracks_only_reviewed_matching_ids(tmp_path):
    import json

    from bodhan_eval.__main__ import _attach_calibration_labels

    reviewed = Item(id="reviewed", source="x", lang="hi", raw={})
    injected = Item(id="injected", source="x", lang="hi", labels=[Label("format")],
                    raw={"labels": [{"type": "format"}]})
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({"id": "reviewed", "labels": []}) + "\n" +
                    json.dumps({"id": "unknown", "labels": []}), encoding="utf-8")
    selected = _attach_calibration_labels({"reviewed": reviewed, "injected": injected}, str(path))
    assert selected == {"reviewed"}
    assert reviewed.raw["labels"] == []
    assert _attach_calibration_labels({"reviewed": reviewed}, None) is None
