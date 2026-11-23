from bodhan_eval.config import load_thresholds
from bodhan_eval.speech import _detect_repetition, _longest_deletion, asr_evidence, transcript_is_degenerate


def test_repetition_counts_are_compared_consistently():
    assert _detect_repetition("aaaaaaaaaaaaaaa", "aaaaaaaaaaaaaaa") is None
    assert _detect_repetition("abcdefgh xyz", "abcdefgh xyz abcdefgh") is not None
    assert _detect_repetition("abcdefgh xyz abcdefgh", "abcdefgh xyz abcdefgh") is None


def test_recognizer_character_loop_is_marked_unreliable():
    assert transcript_is_degenerate("\u0b44" * 186)
    assert not transcript_is_degenerate("hello hello")


def test_omission_requires_a_contiguous_deletion():
    ops = [("delete", 0, 0), ("replace", 1, 0), ("delete", 2, 1), ("equal", 3, 1)]
    assert len(_longest_deletion(ops)) == 1
    evidence = asr_evidence("en", "one two three four five six", "one two three", None,
                            load_thresholds())
    assert any(item.category == "speech_omission" for item in evidence)
