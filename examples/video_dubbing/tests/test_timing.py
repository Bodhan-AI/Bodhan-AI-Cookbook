"""Timing: where each dub may sit, subtitle times, and muting the original under speech."""

import numpy as np
from bodhan_dub.assemble import RATE, _mute_envelope, _srt, _ts
from bodhan_dub.speech import EARLY, GUARD, MAX_BORROW, window

ROWS = [{"start": 3.3, "end": 11.1}, {"start": 11.4, "end": 29.1}, {"start": 29.5, "end": 32.8}, {"start": 56.0, "end": 60.0}]


def test_first_unit_may_start_early_into_leading_silence():
    start, _ = window(ROWS, 0, 70.0)
    assert start == 3.3 - EARLY


def test_neighbours_share_a_short_pause_and_leave_a_guard():
    _, end0 = window(ROWS, 0, 70.0)
    start1, _ = window(ROWS, 1, 70.0)
    assert ROWS[0]["end"] <= end0 < start1 <= ROWS[1]["start"]
    assert round(start1 - end0, 6) == GUARD


def test_a_unit_borrows_at_most_max_borrow_of_a_long_pause():
    _, end = window(ROWS, 2, 70.0)
    assert end == ROWS[2]["end"] + MAX_BORROW


def test_last_unit_ends_a_guard_before_the_video_does():
    _, end = window(ROWS, 3, 61.0)
    assert end == 61.0 - GUARD


def test_windows_never_overlap():
    spans = [window(ROWS, i, 70.0) for i in range(len(ROWS))]
    assert all(a[1] < b[0] for a, b in zip(spans, spans[1:], strict=False))


def test_srt_timestamp_format():
    assert _ts(3725.5) == "01:02:05,500"


def test_srt_shares_a_unit_between_its_sentences_by_length():
    srt = _srt([{"start": 0.0, "end": 6.0, "text": "One two. Three four five."}], "text", "start", "end")
    assert "00:00:00,000 --> 00:00:02,000\nOne two." in srt
    assert "00:00:02,000 --> 00:00:06,000\nThree four five." in srt


def test_mute_envelope_silences_speech_and_keeps_the_rest():
    gain = _mute_envelope([(1.0, 2.0)], 4 * RATE)
    assert gain[int(1.5 * RATE)] == 0
    assert gain[int(0.2 * RATE)] == 1 and gain[int(3.5 * RATE)] == 1
    assert np.all((gain >= 0) & (gain <= 1))
