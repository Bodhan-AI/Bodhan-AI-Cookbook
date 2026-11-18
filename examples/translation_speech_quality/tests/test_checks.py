import json

import pytest
from bodhan_eval.calibration import calibrate, evaluate
from bodhan_eval.checks import (
    deterministic_checks,
    edit_alignment,
    format_flags,
    normalized,
    number_flags,
    numeric_spans,
    reference_metrics,
    script_flags,
    speech_text_flags,
)
from bodhan_eval.inject import inject
from bodhan_eval.media import text_chunks
from bodhan_eval.runner import read_dataset


def test_digit_normalization_retains_quantity():
    assert normalized("  २५  \u200d  ৩৪ ") == "25 34"
    assert number_flags("25 and 34", "२५ এবং ৩৪") == []


def test_numeric_span_is_in_original_text():
    text = "मूल्य २५ रुपये"
    value, (start, end) = numeric_spans(text)[0]
    assert value == "25"
    assert text[start:end] == "२५"


def test_number_mismatch_has_exact_target_evidence():
    findings = number_flags("At 25 degrees", "At 35 degrees")
    supported = [f for f in findings if f["confidence"] >= 0.9]
    assert len(supported) == 1
    assert "At 35 degrees"[slice(*supported[0]["span"])] == "35"


def test_spelled_out_number_stays_uncertain():
    assert all(f["confidence"] < 0.9 for f in number_flags("25 people", "twenty-five people"))


def test_grouped_thousands_and_reordered_dates():
    assert number_flags("1,000 on 2026-11-18", "18-11-2026: १०००") == []


def test_script_does_not_claim_language_from_a_shared_script():
    assert script_flags("हिन्दी भाषा का उदाहरण", "mr") == []
    assert script_flags("An entirely untranslated English paragraph", "ta")[0]["confidence"] < 0.9


def test_copied_english_source_is_stronger_than_romanized_output():
    source = "The meeting starts tomorrow afternoon."
    copied, _ = deterministic_checks({"source": source, "translation": source, "lang": "mr"})
    romanized, _ = deterministic_checks({"source": source, "translation": "Baithak kal dopahar ko shuru hogi.", "lang": "hi"})
    assert any(f["type"] == "wrong_language" and f["confidence"] > 0.9 for f in copied)
    assert all(f["confidence"] < 0.9 for f in romanized)


def test_external_human_labels_report_per_check_metrics():
    rows = [{"id": "one", "candidates": [{"type": "number", "confidence": 0.98, "check": "number_preservation"}]}]
    _, result = calibrate(rows, [{"id": "one", "labels": [{"type": "number"}]}])
    assert result["human_items"] == 1
    assert result["per_check"]["number_preservation"]["number"]["recall"] == 1


@pytest.mark.parametrize("source,target", [
    ("$x + y$", "$x + y"),
    ("[site](https://example.com)", "[site](https://bad.example.com)"),
    ("| A | B |\n|---|---|\n| 1 | 2 |", "| A |\n|---|\n| 1 |"),
    ("`foo()`", "`bar()`"),
])
def test_structure_defects_have_evidence(source, target):
    assert format_flags(source, target)
    assert all(f["evidence"]["expected"] != f["evidence"]["observed"] for f in format_flags(source, target))


def test_metrics_are_reference_based_only():
    assert reference_metrics(None, "test") == {}
    assert reference_metrics("hello", "hello")["chrf++"] == 100


def test_levenshtein_and_speech_evidence():
    assert edit_alignment("abc", "axc")["cer"] == pytest.approx(1 / 3)
    compare, findings = speech_text_flags("one two three four", "one two three")
    assert compare["cer"] > 0
    assert findings[0]["type"] == "speech_omission"


def test_text_chunking_preserves_input_exactly():
    for text in ("word " * 100, "x" * 301, "line\nnext\tword"):
        chunks = text_chunks(text, 50)
        assert "".join(chunks) == text
        assert max(map(len, chunks)) <= 50


def test_invalid_and_duplicate_items_do_not_drop_valid_rows(tmp_path):
    path = tmp_path / "items.jsonl"
    good = {"id": "a", "source": "Hello", "lang": "hi"}
    path.write_text(json.dumps(good) + "\nnot json\n" + json.dumps(good) + "\n")
    rows, invalid = read_dataset(path)
    assert len(rows) == 1 and len(invalid) == 2


def test_injected_labels_never_tune_human_thresholds():
    rows = [{"id": "x", "candidates": []}]
    thresholds, result = calibrate(rows, [{"id": "x", "annotation_source": "programmatic_injection",
                                          "labels": [{"type": "number"}]}])
    assert thresholds["number"] == 0.9
    assert result["human_items"] == 0


def test_empty_categories_are_undefined_not_perfect():
    result = evaluate([{"id": "clean", "flags": []}], [{"id": "clean", "labels": []}])
    assert result["per_category"]["omission"]["recall"] is None


def test_labels_do_not_change_deterministic_verdict():
    base = {"id": "a", "source": "25", "translation": "35", "lang": "hi"}
    assert deterministic_checks(base) == deterministic_checks({**base, "labels": [{"type": "addition"}]})


def test_injector_changes_only_requested_output():
    clean = {"id": "a", "source": "25", "translation": "२५", "lang": "hi"}
    broken = inject(clean, "number")
    assert clean["translation"] == "२५"
    assert broken["translation"] != clean["translation"]
    assert broken["annotation_source"] == "programmatic_injection"
