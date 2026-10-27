import numpy as np
import pytest

from app.segmenter import (
    SegmenterConfig,
    SpeakerTurn,
    normalize_turns,
    split_by_pauses,
    split_by_turns,
)
from tests.conftest import CONVERSATION, SR, synth


def speech_spans(pattern):
    """(start, end) seconds of every "speech" part in a synth pattern."""
    t, spans = 0.0, []
    for kind, seconds in pattern:
        if kind == "speech":
            spans.append((t, t + seconds))
        t += seconds
    return spans


@pytest.mark.parametrize(
    "pattern",
    [
        [("speech", 95.0)],  # one long monologue, no pauses at all
        [("speech", 27.9), ("silence", 0.2), ("speech", 27.9), ("silence", 0.2), ("speech", 10)],
        [("speech", 3.0), ("silence", 0.5)] * 40,
        CONVERSATION,
    ],
)
def test_no_piece_is_ever_longer_than_28_seconds(pattern):
    pieces = split_by_pauses(synth(pattern))
    assert pieces
    assert all(0 < p.duration <= 28.0 for p in pieces)
    assert all(a.end <= b.start for a, b in zip(pieces, pieces[1:], strict=False)), "pieces must not overlap"


def test_cuts_fall_in_pauses_not_inside_words():
    pieces = split_by_pauses(synth(CONVERSATION))
    for p in pieces:
        for s, e in speech_spans(CONVERSATION):
            assert not (s + 0.05 < p.start < e - 0.05), f"piece starts inside speech at {p.start}"
            assert not (s + 0.05 < p.end < e - 0.05), f"piece ends inside speech at {p.end}"


def test_every_word_is_covered():
    pieces = split_by_pauses(synth(CONVERSATION))
    for s, e in speech_spans(CONVERSATION):
        assert any(p.start <= s + 0.05 and p.end >= e - 0.05 for p in pieces), f"speech {s}-{e} not covered"


def test_long_silences_are_dropped():
    pattern = [("silence", 10), ("speech", 3), ("silence", 20), ("speech", 3), ("silence", 10)]
    pieces = split_by_pauses(synth(pattern))
    assert len(pieces) == 2
    assert sum(p.duration for p in pieces) < 8  # 6 s of speech plus padding, not 46 s
    assert pieces[0].start > 9.5 and pieces[1].start > 32.5


def test_long_speech_is_cut_at_the_quietest_moment():
    pattern = [("speech", 20.0), ("dip", 0.2), ("speech", 20.0)]
    pieces = split_by_pauses(synth(pattern))
    assert len(pieces) == 2
    assert pieces[0].end == pytest.approx(20.1, abs=0.3)


def test_short_pauses_join_into_one_piece():
    pattern = [("speech", 2.0), ("silence", 0.5), ("speech", 2.0), ("silence", 0.5), ("speech", 2.0)]
    assert len(split_by_pauses(synth(pattern))) == 1


def test_silence_only_gives_no_pieces():
    assert split_by_pauses(np.zeros(SR * 5, dtype=np.int16)) == []
    assert split_by_pauses(synth([("silence", 5.0)])) == []


def test_custom_max_length_is_respected():
    pieces = split_by_pauses(synth([("speech", 30.0)]), SegmenterConfig(max_len=8.0))
    assert all(p.duration <= 8.0 for p in pieces)


def test_normalize_turns_resolves_overlaps_and_relabels():
    turns = [
        SpeakerTurn(5.0, 9.0, "speaker_1"),
        SpeakerTurn(0.0, 5.5, "speaker_0"),  # overlaps the next turn: the later one wins
        SpeakerTurn(6.0, 7.0, "speaker_0"),  # entirely inside speaker_1's turn: dropped
        SpeakerTurn(9.2, 12.0, "speaker_1"),  # same speaker after a short gap: merged
        SpeakerTurn(12.5, 14.0, "speaker_0"),
    ]
    out = normalize_turns(turns)
    assert [(t.start, t.end, t.speaker) for t in out] == [
        (0.0, 5.0, "Speaker 1"),
        (5.0, 12.0, "Speaker 2"),
        (12.5, 14.0, "Speaker 1"),
    ]


def test_split_by_turns_keeps_each_piece_inside_one_turn():
    audio = synth([("speech", 40.0), ("silence", 1.0), ("speech", 6.0)])
    turns = [SpeakerTurn(0.0, 40.0, "A"), SpeakerTurn(40.0, 47.0, "B")]
    pieces = split_by_turns(audio, turns)
    assert all(p.duration <= 28.0 for p in pieces)
    assert {p.speaker for p in pieces} == {"Speaker 1", "Speaker 2"}
    for p in pieces:
        if p.speaker == "Speaker 1":
            assert p.end <= 40.0
        else:
            assert p.start >= 40.0
