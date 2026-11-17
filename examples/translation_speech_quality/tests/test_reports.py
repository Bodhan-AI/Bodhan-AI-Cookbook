"""Validate calibration, budget-free CLI behavior, and raw report integrity."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from bodhan_eval.calibration import apply_external_labels, calibration_fit, fit_thresholds, measure, split_evaluations
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
        *(labelled_result(f"train-bad-{n}", f"Use 2{n} cm.", known, valid, "calibration") for n in range(3)),
        *(labelled_result(f"train-clean-{n}", f"Use 3{n} cm.", [], None, "calibration") for n in range(3)),
    ]
    holdout = labelled_result("holdout", "Use 40 cm.", known, wrong_span, "holdout")
    calibration, test = split_evaluations([*training, holdout])
    thresholds, support = calibration_fit(calibration)
    assert thresholds["number"] == 0.65
    assert support["number"] == {"positive_items": 3, "negative_items": 3, "fitted": True}
    assert not support["entity"]["fitted"]
    # One positive and one negative are not enough evidence to move a threshold.
    sparse, sparse_support = calibration_fit(calibration[:1] + calibration[3:4])
    assert sparse["number"] == 0.72 and not sparse_support["number"]["fitted"]
    metrics = measure(test, thresholds)
    assert metrics["recall"] == 1
    assert metrics["localization"] == 0
    assert metrics["per_category"]["number"]["fn"] == 1


def test_category_only_match_cannot_displace_localized_evidence():
    """Retain a grounded detection even when a second finding has only a matching category."""
    text = "6 grams. 20 minutes. 100 degrees."
    labels = [
        {"type": "number", "severity": "major", "field": "translation", "span": [0, 7]},
        {"type": "number", "severity": "major", "field": "translation", "span": [9, 19]},
    ]
    actual = Finding("number", "major", Span("translation", 0, 7), "numbers", text[:7], "Wrong mass", 0.88)
    result = labelled_result("multiple", text, labels, actual, "holdout")
    result.candidates.append(Finding("number", "major", Span("translation", 21, 32), "numbers", text[21:32], "Other quantity", 0.88))
    metrics = measure([result], fit_thresholds([]))
    assert metrics["detected_errors"] == 2
    assert metrics["localized_detections"] == 1
    assert metrics["localization"] == 0.5
    assert metrics["category_only_detections"] == 1


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
    labels.write_text(json.dumps({"id": "case", "lang": "en", "labels": [], "annotator": "reviewer-01"}) + "\n", encoding="utf-8")
    [reviewed], problems = apply_external_labels([item], labels)
    assert not problems
    assert reviewed.labels == ()
    assert reviewed.split == "calibration"
    assert (reviewed.label_origin, reviewed.annotator) == ("human", "reviewer-01")
    assert reviewed.seed_labels[0].category == "number"


def test_blind_review_packet_round_trip(tmp_path):
    """Hide mutation identity while accepting the reviewer's opaque ID."""
    from bodhan_eval.demo import review_rows

    row = {"id": "measurement-number", "lang": "hi", "source": "Take 5 steps.", "translation": "6 कदम चलें।", "label_origin": "injected"}
    [entry] = review_rows([row])
    assert set(entry) == {"review_id", "lang", "source", "translation"}
    assert row["id"] not in entry["review_id"]
    path = tmp_path / "review.jsonl"
    path.write_text(json.dumps({"review_id": entry["review_id"], "labels": [], "annotator": "reviewer-01"}) + "\n", encoding="utf-8")

    [reviewed], errors = apply_external_labels([parse_item(row, 1)], path)
    assert not errors
    assert reviewed.labels == ()
    assert (reviewed.label_origin, reviewed.annotator) == ("human", "reviewer-01")


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


def test_baseline_matches_evidence_across_runs(tmp_path):
    """Distinguish a replacement defect from a persistent finding after an offset shift."""
    source = "Use 5 g of salt and 20 g of sugar."

    def evaluate(case_id, candidate):
        """Build real reports from the numeric evidence for the shared source."""
        item = parse_item({"id": case_id, "lang": "en", "source": source, "translation": candidate}, 1)
        result = Evaluation(item, translation=candidate)
        result.candidates.extend(number_findings(source, candidate, "source"))
        result.record("translation_structure")
        return result

    previous = "Use 6 g of salt and 20 g of sugar."
    base = tmp_path / "baseline"
    current = tmp_path / "current"
    write_reports([evaluate("replaced", previous), evaluate("shifted", previous)], [], [], base, {}, None)
    write_reports(
        [
            evaluate("replaced", "Use 5 g of salt and 21 g of sugar."),
            evaluate("shifted", "Next, use 6 g of salt and 20 g of sugar."),
        ],
        [],
        [],
        current,
        {},
        base,
    )
    counts = read_json(current / "summary.json")["baseline"]
    assert counts == {"added_flags": 1, "resolved_flags": 1, "persistent_flags": 1}

    before = evaluate("severity", previous)
    after = evaluate("severity", previous)
    after.candidates[0] = replace(after.candidates[0], severity="critical")
    write_reports([before], [], [], base, {}, None)
    write_reports([after], [], [], current, {}, base)
    assert read_json(current / "summary.json")["baseline"] == {"persistent_flags": 1, "severity_changes": 1}


def test_baseline_uses_expected_quantity_and_completed_checks(tmp_path):
    """Track a changing wrong value and allow independent numeric results on partial rows."""
    source = "Use 5 g of salt."

    def evaluate(case, text, judge_status):
        """Record the outcome of actual numeric checks and the available coverage."""
        item = parse_item({"id": case, "lang": "en", "source": source, "translation": text}, 1)
        result = Evaluation(item, translation=text, candidates=number_findings(source, text, "source"))
        result.record("translation_structure")
        result.record("judge", judge_status)
        return result

    baseline, current = tmp_path / "baseline", tmp_path / "current"
    before = [evaluate("persistent", "Use 6 g of salt.", "ok"), evaluate("partial", "Use 6 g of salt.", "skipped")]
    after = [evaluate("persistent", "Use 7 g of salt.", "ok"), evaluate("partial", source, "skipped")]
    write_reports(before, [], [], baseline, {}, None)
    write_reports(after, [], [], current, {}, baseline)
    assert read_json(current / "summary.json")["baseline"] == {"persistent_flags": 1, "resolved_flags": 1}


def test_baseline_resolves_only_findings_with_shared_check_coverage(tmp_path):
    """Allow completed numeric work while withholding deltas for skipped judge and absent subchecks."""
    source = "Use 5 g of salt."
    wrong = "Use 6 g of salt."
    old_item = parse_item({"id": "example", "lang": "en", "source": source, "translation": wrong}, 1)
    old = Evaluation(old_item, translation=wrong, candidates=number_findings(source, wrong, "source"))
    old.candidates.append(Finding("entity", "minor", Span("translation", 11, 15), "judge", "salt", "Item-level judge evidence", 0.85))
    old.record("translation_structure", checks=["numbers"])
    old.record("judge")
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    write_reports([old], [], [], baseline, {}, None)

    corrected = Evaluation(old_item, translation=source)
    corrected.record("translation_structure", checks=["numbers"])
    corrected.record("judge", "skipped")
    write_reports([corrected], [], [], current, {}, baseline)
    assert read_json(current / "summary.json")["baseline"] == {"incomparable_coverage": 1, "resolved_flags": 1}

    omitted = Evaluation(old_item, translation="")
    omitted.record("translation_structure", checks=["nonempty"])
    omitted.record("judge", "skipped")
    write_reports([omitted], [], [], current, {}, baseline)
    assert read_json(current / "summary.json")["baseline"] == {"incomparable_coverage": 1}


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


def test_malformed_baseline_is_rejected_before_scoring(tmp_path):
    """Reject invalid evidence without opening an output cache or evaluating rows."""
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "summary.json").write_text('{"configuration":{}}', encoding="utf-8")
    (baseline / "items.jsonl").write_text('{"id":"lesson","lang":"hi","status":"scored","checks":{},"flags":[null]}\n', encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"id":"lesson","lang":"hi","source":"Read the first sentence."}\n', encoding="utf-8")
    cache = tmp_path / "cache.sqlite"
    out = tmp_path / "report"
    run = run_cli(tmp_path, dataset, out, "--baseline", str(baseline), "--generate", "--cache", str(cache), languages="hi")
    assert run.returncode == 2
    assert "Invalid baseline evidence" in run.stderr
    assert not cache.exists()
    assert not (out / "items.jsonl").exists()


@pytest.mark.parametrize("filename", ["bad\u0000.wav", "missing.wav"])
def test_cli_keeps_rows_after_invalid_audio_path(tmp_path, filename):
    """Preserve eligible text scoring and subsequent rows when a supplied path fails."""
    rows = [
        {"id": "bad-audio", "source": "Turn to page 18.", "lang": "en", "translation": "Turn to page 8.", "audio": filename},
        {"id": "healthy", "source": "Use 10 cm.", "lang": "en", "translation": "Use 8 cm."},
    ]
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("\n".join(map(json.dumps, rows)) + "\n", encoding="utf-8")
    env = {name: value for name, value in os.environ.items() if name not in CONFIG}
    out = tmp_path / "results"
    run = run_cli(tmp_path, dataset, out, "--max-cost-inr", "0", env=env)
    assert run.returncode == 0, run.stderr
    bad, healthy = [json.loads(line) for line in (out / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert bad["checks"]["audio_input"]["status"] == "error"
    assert bad["flags"][0]["evidence"] == "8"
    assert healthy["id"] == "healthy"
    assert healthy["flags"][0]["evidence"] == "8 cm"


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
    assert read_json(comparison / "summary.json")["baseline"]["persistent_flags"] == 1


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


def test_answered_review_queue_csv_recalibrates(tmp_path):
    """Answers typed into review_queue.csv are read back as attributed human labels."""
    from bodhan_eval.calibration import review_id

    item = parse_item({"id": "q-1", "lang": "hi", "source": "Take 5 steps.", "translation": "6 कदम चलें।"}, 1)
    answers = tmp_path / "answers.csv"
    answers.write_text(
        "review_id,lang,reason,source,translation,audio,verdict,severity,annotator\n"
        f"{review_id('hi', 'q-1')},hi,weak evidence: number,Take 5 steps.,6 कदम चलें।,,Number,Major,reviewer-01\n",
        encoding="utf-8",
    )
    [labelled], errors = apply_external_labels([item], answers)
    assert not errors
    assert [(label.category, label.severity) for label in labelled.labels] == [("number", "major")]
    assert (labelled.label_origin, labelled.annotator) == ("human", "reviewer-01")


def test_table_wide_format_flag_does_not_hide_a_changed_number_inside_it():
    """A strong whole-table format finding and a number finding inside the table are both reported."""
    from bodhan_eval.calibration import accepted
    from bodhan_eval.schema import CATEGORIES

    thresholds = dict.fromkeys(CATEGORIES, 0.72)
    table = Finding("format", "major", Span("translation", 0, 70), "markdown", "| … |", "Table shape changed.", 0.98)
    digit = Finding("number", "major", Span("translation", 40, 44), "numbers", "12 g", "Quantity changed.", 0.88)
    same_words = Finding("mistranslation", "major", Span("translation", 40, 44), "judge", "12 g", "Wrong amount.", 0.85)
    kept = accepted([table, digit, same_words], thresholds)
    assert {finding.category for finding in kept} == {"format", "number"}


def test_judge_finding_inside_exact_damage_is_merged_but_two_exact_findings_are_kept():
    """A judge 'number' on a shifted cell inside a broken table is a consequence of the table damage."""
    from bodhan_eval.calibration import accepted
    from bodhan_eval.schema import CATEGORIES

    thresholds = dict.fromkeys(CATEGORIES, 0.72)
    table = Finding("format", "major", Span("translation", 0, 60), "markdown", "| … |", "Table shape changed.", 0.98)
    judged = Finding("number", "major", Span("translation", 32, 41), "judge", "8 கி.கிராம்", "Value moved to the wrong row.", 0.85)
    exact = Finding("number", "major", Span("translation", 49, 53), "numbers", "13 g", "Quantity changed.", 0.88)
    kept = accepted([table, judged, exact], thresholds)
    assert sorted((finding.category, finding.check) for finding in kept) == [("format", "markdown"), ("number", "numbers")]
