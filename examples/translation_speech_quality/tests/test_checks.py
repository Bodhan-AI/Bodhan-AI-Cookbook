"""Exercise the deterministic checks against known transformations."""

import json
import math
from array import array
from dataclasses import replace

import pytest
from bodhan_eval.audio_checks import (
    SAMPLE_RATE,
    Audio,
    chunks,
    corroborate_speech,
    decode_audio,
    merge_wav,
    pcm_wav,
    signal_checks,
    speech_findings,
    speech_parts,
    speech_recheck_window,
)
from bodhan_eval.calibration import accepted, fit_thresholds
from bodhan_eval.demo import audio_variant, text_variant
from bodhan_eval.judge import judge_input, parse_judgment, verdict_signature
from bodhan_eval.providers import CallError, encoded
from bodhan_eval.schema import Evaluation, Span, parse_item
from bodhan_eval.text_checks import entity_findings, format_findings, normalize_with_offsets, number_findings, script_findings


def tone(seconds: float) -> array:
    """Create a non-speech sinusoidal signal with an exact sample count for signal tests."""
    return array("h", (round(8000 * math.sin(2 * math.pi * 440 * index / SAMPLE_RATE)) for index in range(round(seconds * SAMPLE_RATE))))


def test_unicode_offsets_and_number_normalization():
    """Indic decimal digits compare as values while evidence keeps the source offsets."""
    text = "e\u0301  \u0967\u0966%"
    normalized, mapping = normalize_with_offsets(text)
    assert normalized == "\u00e9 10"
    assert mapping[0] == (0, 2)
    assert text[slice(*mapping[2])] == "\u0967"
    assert number_findings("Use 100 °C.", "Use \u0967\u0966\u0966°C.", "source") == []
    value = "Use \u0967\u0966 °C."
    [finding] = number_findings("Use 100 °C.", value, "source")
    assert finding.category == "number"
    assert value[finding.span.start : finding.span.end] == finding.evidence == "\u0967\u0966 °C"
    assert number_findings("Use 100 cm.", "Use 100 centimeters.", "source") == []
    [mismatched_unit] = number_findings("Use 100 cm.", "Use 100 mm.", "source")
    assert mismatched_unit.evidence == "100 mm"


@pytest.mark.parametrize(
    ("source", "swapped", "reordered"),
    [
        (
            "Mix 5 g of salt with 20 g of sugar.",
            "Mix 20 g of salt with 5 g of sugar.",
            "Mix 20 g of sugar with 5 g of salt.",
        ),
        ("Salt: 5 g\nSugar: 20 g", "Salt: 20 g\nSugar: 5 g", "Sugar: 20 g\nSalt: 5 g"),
        (
            "| Ingredient | Mass |\n| --- | --- |\n| Salt | 5 g |\n| Sugar | 20 g |",
            "| Ingredient | Mass |\n| --- | --- |\n| Salt | 20 g |\n| Sugar | 5 g |",
            "| Ingredient | Mass |\n| --- | --- |\n| Sugar | 20 g |\n| Salt | 5 g |",
        ),
    ],
)
def test_quantity_associations_survive_reordering(source, swapped, reordered):
    """Notice quantity swaps on shared labels while allowing the labels to change order."""
    findings = number_findings(source, swapped, "source")
    assert {finding.evidence for finding in findings} == {"5 g", "20 g"}
    assert {source[flag.anchor.start : flag.anchor.end] for flag in findings} == {"5 g", "20 g"}
    assert number_findings(source, reordered, "source") == []


def test_markdown_tables_and_latex():
    """A translated header can change wording while table and formula syntax remain intact."""
    original = "| Input | Value |\n| --- | --- |\n| Length | $x^2$ |"
    same_shape = "| Reading | Quantity |\n| --- | --- |\n| Distance | $x^2$ |"
    assert format_findings(original, same_shape) == []
    problems = format_findings(original, same_shape.replace("| --- | --- |\n", "").replace("^2", "^3"))
    assert {problem.check for problem in problems} == {"markdown", "latex"}
    # Evidence points at the damaged candidate formula; the source formula is the anchor.
    assert any(
        problem.evidence == "$x^3$" and original[problem.anchor.start : problem.anchor.end] == "$x^2$" for problem in problems
    )


def test_entities_and_script_are_separate_signals():
    """Keep hard identifier losses distinct from uncertain script and acronym cues."""
    findings = entity_findings("Read the NASA page at https://www.nasa.gov/", "Read ESA.", "source")
    assert {(flag.evidence, flag.strength) for flag in findings} == {("NASA", 0.58), ("https://www.nasa.gov/", 0.98)}
    assert script_findings("An untranslated English sentence.", "hi")[0].strength < 0.72
    assert script_findings("An English sentence.", "en") == []


def test_pcm_ranges_and_speech_chunk_limits(tmp_path):
    """Use real ffmpeg decoding and verify silence coordinates and ASR length bounds."""
    original = tone(1.0) + array("h", [0]) * (3 * SAMPLE_RATE) + tone(1.0)
    path = tmp_path / "quiet.wav"
    path.write_bytes(pcm_wav(original))
    audio = decode_audio(path)
    metrics, findings = signal_checks(audio)
    assert metrics["duration_ms"] == 5000
    assert metrics["longest_silence_ms"] == 3000
    assert findings[0].span == Span("audio_ms", 1000, 4000)
    assert not signal_checks(Audio(tone(0.3)))[1]
    assert signal_checks(Audio(array("h", [32767]) * (SAMPLE_RATE // 2)))[1][0].check == "clipping"
    windows = list(chunks(Audio(array("h", [0]) * (61 * SAMPLE_RATE))))
    assert all(0 < duration <= 28_000 for _, duration in windows)
    assert sum(duration for _, duration in windows) == 61_000
    assert merge_wav([pcm_wav(tone(0.1)), pcm_wav(tone(0.2))]) == pcm_wav(tone(0.1) + tone(0.2))
    assert all(len(part) <= 40 for part in speech_parts("word " * 100, limit=40))


def test_speech_alignment_reports_original_text():
    """Recognize sustained missing and repeated spans and preserve original punctuation."""
    expected = "Read the first paragraph. Return to the classroom after the bell."
    metrics, flagged = speech_findings(expected, "Read the first paragraph.", 1000)
    assert metrics["deletions"] > 0
    assert flagged[0].category == "speech_omission"
    assert expected[flagged[0].span.start : flagged[0].span.end] == flagged[0].evidence
    _, repeated = speech_findings(expected, expected + " Return to the classroom after the bell.", 9000)
    assert any(flag.category == "speech_repetition" for flag in repeated)


def test_short_speech_omission_requires_corroboration():
    """Promote a repeatable missing sentence and suppress it if the alternate ASR recovers it."""
    expected = "Read the first paragraph. Return to the classroom after the bell."
    prefix = "Read the first paragraph."
    duration = 5_000
    _, first = speech_findings(expected, prefix, duration, script_field="source")
    thresholds = fit_thresholds([])
    # Over half the script missing in one block is strong evidence on its own (0.86).
    [initial] = accepted(first, thresholds)
    assert initial.strength == 0.86
    seconds = speech_recheck_window(duration)
    clip = Audio(tone(5))
    assert len(list(chunks(clip))) == 1
    assert len(list(chunks(clip, seconds))) > 1

    updated, confirmed, recovered = corroborate_speech(expected, prefix, duration, first, script_field="source")
    [finding] = accepted(updated, thresholds)
    assert finding.category == "speech_omission"
    assert finding.span.field == "source"
    assert expected[finding.span.start : finding.span.end] == "Return to the classroom after the bell"
    assert confirmed == ["speech_omission"] and recovered == 0

    updated, confirmed, recovered = corroborate_speech(expected, expected, duration, first, script_field="source")
    assert accepted(updated, thresholds) == []
    assert confirmed == [] and recovered == 1

    # An unusable second transcript neither confirms nor clears the first evidence.
    updated, confirmed, recovered = corroborate_speech(expected, "", duration, first, script_field="source")
    assert updated == first
    assert confirmed == [] and recovered == 0


def test_judge_validation_uses_real_offsets():
    """The schema parser accepts a justified reference and rejects invalid coordinates."""
    item = parse_item({"id": "speech", "lang": "en", "source": "Keep all 18 pages."}, 1)
    evaluation = Evaluation(item, translation="Keep all 8 pages.")
    _, segments = judge_input(evaluation, None, False)
    assert segments["s2"].numbered() == "[0]Keep [1]all [2]8 [3]pages."
    observation = {
        "category": "number",
        "severity": "major",
        "segment": "s2",
        "first_word": 2,
        "last_word": 2,
        "strength": "clear",
        "reason": "The page count changed.",
    }
    findings, verify, rejected = parse_judgment(
        encoded(
            {
                "findings": [observation, {**observation, "last_word": 500}],
                "verify_speech": False,
            }
        ),
        segments,
    )
    assert not verify and rejected == 1
    assert findings[0].evidence == "8"
    assert findings[0].span == Span("translation", 9, 10)
    assert verdict_signature(findings) != verdict_signature([replace(findings[0], severity="minor")])
    assert verdict_signature(findings) != verdict_signature([replace(findings[0], span=Span("translation", 8, 9))])
    with pytest.raises(CallError, match="findings schema"):
        parse_judgment(b"{}", segments)


def test_judge_reply_tolerates_wrappers_and_keeps_indic_free_reasons():
    """Reasoning blocks, prose, aliases and Indic quotations do not discard a located finding."""
    item = parse_item({"id": "tube", "lang": "hi", "source": "Place the open end in the water."}, 1)
    evaluation = Evaluation(item, translation="ट्यूब के बंद सिरे को पानी में रखें।")
    _, segments = judge_input(evaluation, None, False)
    assert segments["s2"].numbered().startswith("[0]ट्यूब [1]के [2]बंद")
    verdict = {
        "findings": [
            {
                "category": "Mistranslation",
                "severity": "MAJOR",
                "segment": "s2",
                "first_word": 2,
                "last_word": 2,
                "reason": "बंद means closed, but the source says open.",
            }
        ]
    }
    reply = "<think>check polarity</think>\nHere is my verdict:\n```json\n" + json.dumps(verdict, ensure_ascii=False) + "\n```"
    findings, verify, rejected = parse_judgment(reply.encode(), segments)
    assert (verify, rejected) == (False, 0)
    [finding] = findings
    assert (finding.category, finding.severity, finding.strength) == ("mistranslation", "major", 0.85)
    assert evaluation.translation[finding.span.start : finding.span.end] == "बंद"
    assert finding.evidence == "बंद"
    assert all(ord(char) < 128 or not char.isalpha() for char in finding.detail)
    assert "closed" in finding.detail


def test_judge_segments_split_long_lines_on_word_boundaries():
    """Long passages become several short segments whose words map back to the original text."""
    source = " ".join(f"w{index}" for index in range(130))
    item = parse_item({"id": "long", "lang": "en", "source": source}, 1)
    _, segments = judge_input(Evaluation(item), None, False)
    assert len(segments) == 3
    for segment in segments.values():
        for start, end in segment.words:
            assert source[segment.offset + start : segment.offset + end] == segment.text[start:end]


def test_injector_changes_actual_content(tmp_path):
    """Numeric edits and audio corruption produce real variants with provenance and spans."""
    row = {"source": "Measure 20 cm.", "translation": "Measure 20 cm.", "lang": "en"}
    changed, label, method = text_variant(row, "number", choice=0)
    assert changed == "Measure 30 cm."
    assert changed[slice(*label["span"])] == "30 cm"
    assert method == "alter_original_digit"
    audio = tmp_path / "original.wav"
    audio.write_bytes(pcm_wav(tone(2.0)))
    altered, label, method = audio_variant(audio, "garbled_audio", choice=0)
    variant = tmp_path / "changed.wav"
    variant.write_bytes(altered)
    metrics, findings = signal_checks(decode_audio(variant))
    assert metrics["duration_ms"] == 5000
    assert findings[0].span == Span("audio_ms", *label["span"])
    assert method == "insert_three_seconds_of_silence"
