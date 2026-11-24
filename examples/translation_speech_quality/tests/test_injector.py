"""The error injector edits structure programmatically and the English source semantically."""

import math
from array import array

from bodhan_eval.audio_checks import SAMPLE_RATE, decode_audio, pcm_wav
from bodhan_eval.demo import MEANING_SWAPS, audio_variant, format_edits, perturbed_source, text_variant


def tone(seconds: float, amplitude: int = 9000) -> array:
    """Build a speech-level test tone."""
    return array("h", (int(amplitude * math.sin(2 * math.pi * 220 * n / SAMPLE_RATE)) for n in range(int(seconds * SAMPLE_RATE))))


def test_format_edits_break_tables_emphasis_and_math_with_candidate_spans():
    """Each structural edit removes markup only, and its span points at the damaged candidate."""
    candidate = "## शीर्षक\n\n**ध्यान दें** कि $x^2$ धनात्मक है।\n\n| क | ख |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    methods = {method: (changed, label) for changed, label, method in format_edits(candidate)}
    assert set(methods) == {"break_formula_delimiter", "remove_table_separator", "drop_table_cell", "drop_bold_closer", "remove_heading_marker"}
    changed, label = methods["drop_table_cell"]
    assert changed.endswith("| 3 |") or changed.count("|") == candidate.count("|") - 1
    for changed, label in methods.values():
        assert changed != candidate
        assert 0 <= label["span"][0] < label["span"][1] <= len(changed) + 1
        # No target-language word is added or rewritten. Only markup characters disappear.
        assert set(changed) <= set(candidate) | {" "}


def test_wrong_language_can_leave_one_sentence_untranslated():
    """The untranslated variant inserts the aligned English sentence and labels exactly that span."""
    row = {
        "id": "x",
        "lang": "hi",
        "source": "Heat the water. Stir it slowly.",
        "translation": "पानी गरम करें। इसे धीरे-धीरे हिलाएँ।",
    }
    changed, label, method = text_variant(row, "wrong_language", choice=1)
    assert method == "leave_sentence_untranslated"
    assert changed[slice(*label["span"])] in ("Heat the water.", "Stir it slowly.")


def test_source_perturbations_are_english_only_and_labelled():
    """Omission, addition, entity and meaning edits all happen on the English source."""
    row = {"id": "r", "lang": "hi", "source": "NASA launched the probe. It reached orbit before noon.", "translation": "-"}
    shortened, span = perturbed_source(row, "omission")
    assert span is not None and row["source"][span[0] : span[1]] not in shortened
    added, _ = perturbed_source(row, "addition")
    assert added.startswith(row["source"]) and len(added) > len(row["source"])
    renamed, _ = perturbed_source(row, "entity")
    assert "NASA" not in renamed
    flipped, _ = perturbed_source(row, "mistranslation")
    assert "after noon" in flipped
    assert all(word.isascii() for word in MEANING_SWAPS)


def test_audio_variants_cover_partial_repetition_cut_and_noise(tmp_path):
    """Repetition loops a middle phrase, omission removes speech, garbling inserts noise."""
    path = tmp_path / "clip.wav"
    original = tone(4.0)
    path.write_bytes(pcm_wav(original))
    repeated, label, method = audio_variant(path, "speech_repetition", choice=1)
    assert method == "repeat_middle_phrase"
    assert len(decode_audio_bytes(tmp_path, repeated)) == len(original) * 3 // 2
    assert label["field"] == "audio_ms" and label["span"][1] - label["span"][0] == 2000
    shortened, _, method = audio_variant(path, "speech_omission", choice=1)
    assert method == "remove_middle_audio" and len(decode_audio_bytes(tmp_path, shortened)) < len(original)
    noisy, label, method = audio_variant(path, "garbled_audio", choice=1)
    assert method == "insert_two_seconds_of_noise"
    assert len(decode_audio_bytes(tmp_path, noisy)) == len(original) + 2 * SAMPLE_RATE


def decode_audio_bytes(tmp_path, data: bytes) -> array:
    """Decode WAV bytes through the harness decoder."""
    path = tmp_path / "variant.wav"
    path.write_bytes(data)
    return decode_audio(path).samples


def test_splice_variants_reuse_existing_translations_only():
    """Offline omission and addition move whole Bodhan sentences and label the aligned span."""
    from bodhan_eval.demo import splice_variant

    row = {"id": "a", "lang": "hi", "source": "Heat the water. Stir it slowly.", "translation": "पानी गरम करें। इसे धीरे-धीरे हिलाएँ।"}
    donor = {"id": "b", "lang": "hi", "source": "Close the lid.", "translation": "ढक्कन बंद कर दीजिए।"}
    shorter, label, method = splice_variant(row, "omission", [row, donor])
    assert method == "delete_translated_sentence" and len(shorter) < len(row["translation"])
    assert row["source"][slice(*label["span"])] in ("Heat the water.", "Stir it slowly.")
    longer, label, method = splice_variant(row, "addition", [row, donor])
    assert longer[slice(*label["span"])] == "ढक्कन बंद कर दीजिए।"
    assert splice_variant(row, "addition", [row]) is None


def test_signal_check_flags_inserted_noise_but_not_clean_tone(tmp_path):
    """Two seconds of broadband noise inside speech-level audio is localized as garbled audio."""
    from bodhan_eval.audio_checks import signal_checks
    from bodhan_eval.schema import Span

    path = tmp_path / "clip.wav"
    path.write_bytes(pcm_wav(tone(4.0)))
    _, clean = signal_checks(decode_audio(path))
    assert clean == []
    noisy, label, _ = audio_variant(path, "garbled_audio", choice=1)
    variant = tmp_path / "noisy.wav"
    variant.write_bytes(noisy)
    metrics, findings = signal_checks(decode_audio(variant))
    [finding] = [f for f in findings if f.check == "noise"]
    assert finding.strength >= 0.72 and metrics["longest_noise_ms"] >= 1900
    labelled = Span("audio_ms", *label["span"])
    assert finding.span.start < labelled.end and labelled.start < finding.span.end
