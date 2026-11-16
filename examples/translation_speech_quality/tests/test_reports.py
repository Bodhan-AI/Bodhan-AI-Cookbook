"""Validate calibration, budget-free CLI behavior, and raw report integrity."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from bodhan_eval.calibration import apply_external_labels, fit_thresholds, measure, split_evaluations
from bodhan_eval.report import write_reports
from bodhan_eval.schema import Evaluation, Finding, Span, parse_item
from bodhan_eval.text_checks import number_findings

RECIPE = Path(__file__).resolve().parents[1]
REPORTS = ("items.jsonl", "summary.json", "summary.md", "calibration.json")
CONFIG = (
    "BODHAN_TRANSLATE_KEY",
    "BODHAN_SPEAK_KEY",
    "BODHAN_TRANSCRIBE_KEY",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
)


def labelled_result(id_, text, labels, candidate, split):
    """Construct labelled observations for pure matching and threshold tests."""
    item = parse_item(
        {
            "id": id_,
            "source": text,
            "lang": "en",
            "translation": text,
            "labels": labels,
            "label_origin": "human",
            "split": split,
        },
        1,
    )
    result = Evaluation(item, translation=text)
    result.record("judge")
    if candidate:
        result.candidates.append(candidate)
    return result


def test_calibration_and_localization_are_independent():
    """Fit on reviewed examples and count a correctly typed wrong span as a localization miss."""
    known = [{"type": "number", "severity": "major", "field": "translation", "span": [4, 6]}]
    valid = Finding("number", "major", Span("translation", 4, 6), "numbers", "20", "quantity changed", 0.68)
    wrong_span = Finding("number", "major", Span("translation", 8, 10), "numbers", "10", "quantity changed", 0.68)
    training = [
        labelled_result("train-bad", "Use 20 cm.", known, valid, "calibration"),
        labelled_result("train-clean", "Use 30 cm.", [], None, "calibration"),
    ]
    holdout = labelled_result("holdout", "Use 40 cm.", known, wrong_span, "holdout")
    calibration, test = split_evaluations([*training, holdout])
    thresholds = fit_thresholds(calibration)
    assert thresholds["number"] == 0.65
    metrics = measure(test, thresholds)
    assert metrics["recall"] == 1
    assert metrics["localization"] == 0
    assert metrics["per_category"]["number"]["fn"] == 1


def test_unlabelled_rows_never_inflate_clean_metrics():
    """A dataset row with missing labels is unknown and stays out of false-alarm counts."""
    item = parse_item({"id": "one", "source": "A sentence.", "lang": "en"}, 1)
    result = Evaluation(item)
    assert measure([result], fit_thresholds([]))["reviewed_clean_items"] == 0


def test_external_labels_preserve_the_injected_mutation(tmp_path):
    """Human review can override calibration labels while keeping independent seed provenance."""
    item = parse_item(
        {
            "id": "case",
            "lang": "en",
            "source": "Take 5 steps.",
            "label_origin": "injected",
            "labels": [{"type": "number", "severity": "major", "span": [5, 6], "field": "source"}],
        },
        1,
    )
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"id": "case", "lang": "en", "labels": []}) + "\n", encoding="utf-8")
    [reviewed], problems = apply_external_labels([item], labels)
    assert not problems
    assert reviewed.labels == ()
    assert reviewed.seed_labels[0].category == "number"


def test_baseline_shows_resolved_and_new_quality_findings(tmp_path):
    """Compare two complete runs of the deterministic numeric check on the same keys."""

    def text_result(case_id, value):
        """Run the actual numeric check on a supplied candidate."""
        item = parse_item({"id": case_id, "lang": "en", "source": "Use 100 grams.", "translation": value}, 1)
        result = Evaluation(item, translation=value)
        result.candidates.extend(number_findings(item.source, value, "source"))
        result.record("translation_structure")
        return result

    before = [text_result("resolved", "Use 10 grams."), text_result("new", "Use 100 grams.")]
    after = [text_result("resolved", "Use 100 grams."), text_result("new", "Use 500 grams.")]
    first, second = tmp_path / "before", tmp_path / "after"
    write_reports(before, [], [], first, {}, None)
    write_reports(after, [], [], second, {}, first)
    summary = read_json(second / "summary.json")
    assert summary["baseline"]["added_flags"] == 1
    assert summary["baseline"]["resolved_flags"] == 1
    write_reports(after, [], [], second, {}, None)
    assert not (second / "diff.md").exists()


def read_json(path):
    """Read a UTF-8 report object."""
    return json.loads(path.read_text(encoding="utf-8"))


def run_cli(cwd, dataset, output, *flags, languages="en", env=None):
    """Invoke the public reviewer command in a separate Python process."""
    return subprocess.run(
        [sys.executable, "-m", "bodhan_eval", "--dataset", str(dataset), "--langs", languages, "--out", str(output), *flags],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_cli_ten_real_input_cases_without_credentials(tmp_path):
    """Process supplied translations and a corrupt row without inventing model responses."""
    dataset = tmp_path / "dataset.jsonl"
    rows = [
        {
            "id": str(index),
            "source": f"The class has {index + 20} students.",
            "lang": "en",
            "translation": f"The class has {index + 20 if index != 0 else 10} students.",
        }
        for index in range(10)
    ]
    dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n{broken json}\n", encoding="utf-8")
    env = {name: value for name, value in os.environ.items() if name not in CONFIG}
    cold, replay, comparison = (tmp_path / name for name in ("cold", "replay", "comparison"))
    for output in (cold, replay):
        result = run_cli(tmp_path, dataset, output, "--max-cost-inr", "0", env=env)
        assert result.returncode == 0, result.stderr
    summary = read_json(cold / "summary.json")
    assert summary["counts"] == {"invalid": 1, "partial": 10}
    assert summary["findings"]["number"] == 1
    assert summary["cost"]["bodhan_inr"] == 0
    rows = [json.loads(line) for line in (cold / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["checks"]["judge"]["status"] == "skipped"
    assert all((cold / name).read_bytes() == (replay / name).read_bytes() for name in REPORTS)
    final_event = json.loads((replay / "run.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert final_event["requests"] == 0
    diff = run_cli(tmp_path, dataset, comparison, "--baseline", str(cold), env=env)
    assert diff.returncode == 0, diff.stderr
    assert "Incomplete or changed check coverage" in (comparison / "diff.md").read_text(encoding="utf-8")


@pytest.mark.skipif(os.getenv("BODHAN_LIVE_TEST") != "1", reason="live APIs cost credit and require explicit test opt-in")
def test_live_ten_items_with_cache(tmp_path):
    """Verify all production endpoints on ten original texts and a zero-request replay."""
    cold, replay = tmp_path / "cold", tmp_path / "replay"
    cache = tmp_path / "cache.sqlite"
    for output in (cold, replay):
        result = run_cli(
            RECIPE,
            RECIPE / "data/demo.jsonl",
            output,
            "--generate",
            "--limit",
            "10",
            "--cache",
            str(cache),
            "--max-cost-inr",
            "5",
            languages="hi,ta,bn",
        )
        assert result.returncode == 0, result.stderr
    cold_rows = [json.loads(line) for line in (cold / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(cold_rows) == 10
    for row in cold_rows:
        assert row["checks"]["translation_input"] == {"origin": "generated", "status": "ok"}
        assert row["checks"]["audio_input"]["origin"] == "generated"
        assert row["checks"]["transcription"]["status"] == "ok"
        assert row["checks"]["judge"]["status"] == "ok"
    assert all((cold / name).read_bytes() == (replay / name).read_bytes() for name in REPORTS)
    final_event = json.loads((replay / "run.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert final_event["requests"] == 0 and final_event["cache_hits"] > 0
