from __future__ import annotations

import numpy as np
import pytest

from bodhan_dub import vad
from bodhan_dub.vad import VadParams
from tests.conftest import SR, build_signal, silence


def test_frame_db_reports_loud_frames_higher_than_silence() -> None:
    loud = np.full(SR, 0.5, dtype=np.float32)
    quiet = np.full(SR, 0.001, dtype=np.float32)
    assert vad.frame_db(loud, SR).mean() > vad.frame_db(quiet, SR).mean() + 40


def test_empty_audio_has_no_regions() -> None:
    assert vad.detect(np.zeros(0, dtype=np.float32), SR) == []


def test_pure_silence_has_no_regions() -> None:
    assert vad.detect(silence(3.0), SR) == []


def test_continuous_speech_with_no_pauses_is_still_detected() -> None:
    # A flat, unbroken signal has no usable dynamic range. The VAD must not
    # mistake "no quiet parts" for "no speech".
    samples = np.full(SR * 5, 0.3, dtype=np.float32)
    regions = vad.detect(samples, SR)

    assert len(regions) == 1
    assert regions[0].duration_s > 4.0


def test_detects_two_utterances_in_place() -> None:
    samples = build_signal([(1.0, 2.5), (3.3, 4.5)], total_s=6.0)
    regions = vad.detect(samples, SR, VadParams(pad_s=0.0, tail_pad_s=0.0))

    assert len(regions) == 2
    assert regions[0].start_s == pytest.approx(1.0, abs=0.08)
    assert regions[0].end_s == pytest.approx(2.5, abs=0.08)
    assert regions[1].start_s == pytest.approx(3.3, abs=0.08)
    assert regions[1].end_s == pytest.approx(4.5, abs=0.08)


def test_short_gap_does_not_split_one_utterance() -> None:
    # A 120 ms dip inside a word, well under the 300 ms bridge.
    samples = build_signal([(1.0, 2.0), (2.12, 3.0)], total_s=4.0)
    regions = vad.detect(samples, SR, VadParams(pad_s=0.0, tail_pad_s=0.0))

    assert len(regions) == 1
    assert regions[0].duration_s > 1.9


def test_tail_padding_extends_into_following_silence() -> None:
    # A final consonant is quiet, so the region should reach past the last
    # frame above the threshold.
    samples = build_signal([(1.0, 2.0), (3.0, 4.0)], total_s=5.0)
    regions = vad.detect(samples, SR, VadParams(pad_s=0.0, tail_pad_s=0.22))

    assert regions[0].end_s == pytest.approx(2.22, abs=0.05)


def test_tail_padding_never_crosses_the_next_region() -> None:
    samples = build_signal([(1.0, 2.0), (2.6, 3.0)], total_s=4.0)
    regions = vad.detect(samples, SR, VadParams(pad_s=0.0, tail_pad_s=0.5))

    assert len(regions) == 2
    assert regions[0].end_s <= regions[1].start_s + 1e-6
    assert regions[0].end_s < 2.6


def test_long_pause_does_split() -> None:
    samples = build_signal([(1.0, 2.0), (2.6, 3.4)], total_s=4.0)
    regions = vad.detect(samples, SR, VadParams(pad_s=0.0, tail_pad_s=0.0))

    assert len(regions) == 2


def test_blip_shorter_than_min_speech_is_dropped() -> None:
    samples = build_signal([(1.0, 1.05), (2.0, 3.0)], total_s=4.0)
    regions = vad.detect(samples, SR, VadParams(pad_s=0.0, tail_pad_s=0.0))

    assert len(regions) == 1
    assert regions[0].start_s == pytest.approx(2.0, abs=0.08)


def test_padding_never_leaves_the_file() -> None:
    samples = build_signal([(0.05, 0.5), (3.7, 4.0)], total_s=4.0)
    regions = vad.detect(samples, SR, VadParams(pad_s=0.5))

    assert regions[0].start_s >= 0.0
    assert regions[-1].end_s <= 4.0


def test_regions_are_sorted_and_disjoint() -> None:
    samples = build_signal([(0.5, 1.2), (1.4, 2.0), (2.5, 3.0)], total_s=4.0)
    regions = vad.detect(samples, SR, VadParams(pad_s=0.1))

    for earlier, later in zip(regions, regions[1:], strict=False):
        assert earlier.end_s <= later.start_s + 1e-6
