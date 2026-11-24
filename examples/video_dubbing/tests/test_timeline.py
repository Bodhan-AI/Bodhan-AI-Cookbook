from __future__ import annotations

import numpy as np
import pytest

from bodhan_dub import timeline


def test_clip_lands_at_its_sample_offset() -> None:
    sr = 1000
    clip = np.full(100, 0.5, dtype=np.float32)

    track = timeline.build_timeline([(2.0, clip)], total_s=5.0, sample_rate=sr)

    assert len(track) == 5000
    assert track[2000] == pytest.approx(0.5)
    assert track[1999] == 0.0
    assert track[2099] == pytest.approx(0.5)
    assert track[2100] == 0.0


def test_timeline_is_exactly_the_requested_length() -> None:
    sr = 16_000
    track = timeline.build_timeline([], total_s=3.5, sample_rate=sr)

    assert len(track) == int(round(3.5 * sr))
    assert not track.any()


def test_overlapping_clips_mix_additively_and_clip() -> None:
    sr = 1000
    a = np.full(100, 0.6, dtype=np.float32)
    b = np.full(100, 0.6, dtype=np.float32)

    track = timeline.build_timeline([(0.0, a), (0.05, b)], total_s=1.0, sample_rate=sr)

    assert track.max() <= 1.0
    assert track[60] == pytest.approx(1.0)


def test_clips_are_never_truncated_at_the_start() -> None:
    sr = 1000
    clip = np.ones(100, dtype=np.float32)

    track = timeline.build_timeline([(-1.0, clip)], total_s=1.0, sample_rate=sr)

    assert track[0] == pytest.approx(1.0)


def test_a_clip_past_the_end_is_trimmed_not_padded() -> None:
    sr = 1000
    clip = np.ones(1000, dtype=np.float32)

    track = timeline.build_timeline([(0.9, clip)], total_s=1.0, sample_rate=sr)

    assert len(track) == 1000
    assert track[-1] == pytest.approx(1.0)
