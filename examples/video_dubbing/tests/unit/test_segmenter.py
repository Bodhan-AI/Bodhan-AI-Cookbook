"""
Unit tests for deterministic sentence segmenter and slot/slack calculation.
"""

from bodhan_dub.stages.asr import TranscribedSegment
from bodhan_dub.stages.segment import Segmenter, compute_slacks, is_sentence_end


def test_is_sentence_end():
    assert is_sentence_end("Hello world.")
    assert is_sentence_end("Is this working?")
    assert is_sentence_end("Great!")
    assert not is_sentence_end("Hello world")
    assert not is_sentence_end("In the morning,")


def test_deterministic_merge_fragments():
    segmenter = Segmenter()
    raw = [
        TranscribedSegment(
            text="The class starts", start=1.0, end=2.2, timestamp_source="api_verbose"
        ),
        TranscribedSegment(
            text="at nine o'clock.", start=2.4, end=3.5, timestamp_source="api_verbose"
        ),
    ]

    merged = segmenter.segment_deterministic(raw)
    assert len(merged) == 1
    assert merged[0].text == "The class starts at nine o'clock."
    assert merged[0].start == 1.0
    assert merged[0].end == 3.5


def test_deterministic_split_long_sentences():
    segmenter = Segmenter(max_sentence_duration=10.0)
    # Long segment of 16 seconds with a comma
    raw = [
        TranscribedSegment(
            text="Artificial intelligence is transforming modern industries, and machine learning enables systems to learn from data.",
            start=0.0,
            end=16.0,
            timestamp_source="api_verbose",
        )
    ]

    split_segs = segmenter.segment_deterministic(raw)
    assert len(split_segs) == 2
    assert split_segs[0].duration < 12.0
    assert split_segs[1].duration < 12.0
    assert split_segs[0].start == 0.0
    assert split_segs[1].end == 16.0


def test_compute_slacks_with_guard_band():
    segments = [
        TranscribedSegment(
            text="First sentence.", start=1.0, end=3.0, timestamp_source="api_verbose"
        ),
        TranscribedSegment(
            text="Second sentence.", start=3.5, end=6.0, timestamp_source="api_verbose"
        ),
    ]

    # Guard band is 80ms (0.080s)
    # Gap between seg 0 and seg 1 is 3.5 - 3.0 = 0.500s
    # slack_after for seg 0 = 0.500 - 0.080 = 0.420s
    # slack_before for seg 1 = 0.500 - 0.080 = 0.420s
    slots = compute_slacks(segments, total_audio_duration=8.0)
    assert len(slots) == 2

    assert slots[0].slot == [1.0, 3.0]
    assert slots[0].slack_after == 0.420
    assert slots[1].slack_before == 0.420
    assert slots[0].max_leading_shift == 0.250
