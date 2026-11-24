import json

from bodhan_eval.budget import Budget
from bodhan_eval.checks.reference import score_reference
from bodhan_eval.dataset import DatasetResult
from bodhan_eval.models import Item
from bodhan_eval.pipeline import run_pipeline
from bodhan_eval.report import write_outputs


def test_reference_absent_or_empty_has_no_score():
    assert score_reference("translation", None) is None
    assert score_reference("translation", "") is None
    assert score_reference("", "reference") is None


def test_reference_identical_is_skipped_and_difference_is_scored():
    same = score_reference("water boils at 100", "water boils at 100")
    different = score_reference("water boils at 100", "cats sleep quietly")
    assert same == {"reference_metric": "self_reference_skipped"}
    assert different is not None
    assert different["chrf"] < 100.0
    assert different["bleu"] < 100.0


def test_reference_identical_after_nfc_is_skipped():
    assert score_reference("e\u0301", "\u00e9") == {
        "reference_metric": "self_reference_skipped"
    }


def test_reference_scores_are_reported_per_language_without_becoming_flags(tmp_path):
    items = [
        Item(
            id="1",
            lang="hi",
            source="Water boils.",
            translation="पानी उबलता है।",
            reference="पानी उबलता है।",
        ),
        Item(
            id="2",
            lang="hi",
            source="Water freezes.",
            translation="पानी जमता है।",
            reference="पानी ठंडा होता है।",
        ),
    ]
    records = run_pipeline(DatasetResult(items=items, total_input_lines=2), Budget(1.0))
    assert records[0]["reference_scores"] == {
        "reference_metric": "self_reference_skipped"
    }
    assert records[1]["reference_scores"] is not None
    assert "chrf" in records[1]["reference_scores"]
    assert all(not any(flag["category"] == "reference" for flag in record["flags"]) for record in records)
    write_outputs(tmp_path, records, Budget(1.0), input_lines=2, seed=7)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["reference_scores"]["hi"]["n"] == 1
    assert "chrf_ci95" in summary["reference_scores"]["hi"]
