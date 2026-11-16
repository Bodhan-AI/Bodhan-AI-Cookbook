"""Exercise the deterministic checks against known transformations."""

import math
from array import array

import pytest
from bodhan_eval.audio_checks import SAMPLE_RATE, Audio, chunks, decode_audio, merge_wav, pcm_wav, signal_checks, speech_findings, speech_parts
from bodhan_eval.demo import audio_variant, text_variant
from bodhan_eval.judge import judge_input, parse_judgment
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


def test_markdown_tables_and_latex():
    """A translated header can change wording while table and formula syntax remain intact."""
    original = "| Input | Value |\n| --- | --- |\n| Length | $x^2$ |"
    same_shape = "| Reading | Quantity |\n| --- | --- |\n| Distance | $x^2$ |"
    assert format_findings(original, same_shape) == []
    problems = format_findings(original, same_shape.replace("| --- | --- |\n", "").replace("^2", "^3"))
    assert {problem.check for problem in problems} == {"markdown", "latex"}
    assert any(problem.evidence == "$x^2$" for problem in problems)


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


def test_judge_validation_uses_real_offsets():
    """The schema parser accepts a justified reference and rejects invalid coordinates."""
    item = parse_item({"id": "speech", "lang": "en", "source": "Keep all 18 pages."}, 1)
    evaluation = Evaluation(item, translation="Keep all 8 pages.")
    _, segments = judge_input(evaluation, None, False)
    observation = {
        "category": "number",
        "severity": "major",
        "segment": "s2",
        "start": 9,
        "end": 10,
        "strength": "clear",
        "reason": "The page count changed.",
    }
    findings, verify, rejected = parse_judgment(
        encoded(
            {
                "findings": [observation, {**observation, "end": 500}],
                "verify_speech": False,
            }
        ),
        segments,
    )
    assert not verify and rejected == 1
    assert findings[0].evidence == "8"
    with pytest.raises(CallError, match="findings schema"):
        parse_judgment(b"{}", segments)


def test_injector_changes_actual_content(tmp_path):
    """Numeric edits and audio corruption produce real variants with provenance and spans."""
    row = {"source": "Measure 20 cm.", "translation": "Measure 20 cm.", "lang": "en"}
    changed, label, method = text_variant(row, "number")
    assert changed == "Measure 30 cm."
    assert changed[slice(*label["span"])] == "30 cm"
    assert method == "alter_original_digit"
    audio = tmp_path / "original.wav"
    audio.write_bytes(pcm_wav(tone(2.0)))
    altered, label, method = audio_variant(audio, "garbled_audio")
    variant = tmp_path / "changed.wav"
    variant.write_bytes(altered)
    metrics, findings = signal_checks(decode_audio(variant))
    assert metrics["duration_ms"] == 5000
    assert findings[0].span == Span("audio_ms", *label["span"])
    assert method == "insert_three_seconds_of_silence"
