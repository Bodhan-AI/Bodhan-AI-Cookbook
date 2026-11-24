"""VAD post-processing and the energy fallback, on hand-made probabilities and synthetic audio."""

import numpy as np
import pytest

from bodhan_dub import vad
from bodhan_dub.vad import FRAME_S, SAMPLE_RATE, Region, VadParams

NO_PAD = VadParams(pad_s=0.0)


def frames(*runs):
    """frames((0.0, 10), (0.9, 20)) -> 10 frames at 0.0 then 20 at 0.9."""
    return np.concatenate([np.full(n, p, dtype=np.float32) for p, n in runs])


def regions(probs, params=NO_PAD):
    return vad.probabilities_to_regions(probs, params, duration_s=len(probs) * FRAME_S)


def test_hysteresis_keeps_speech_through_a_dip_above_off_threshold():
    probs = frames((0.0, 10), (0.9, 20), (0.4, 5), (0.9, 20), (0.0, 10))
    assert regions(probs) == [Region(round(10 * FRAME_S, 3), round(55 * FRAME_S, 3))]


def test_short_pause_is_bridged_long_pause_splits():
    short_gap = frames((0.9, 20), (0.0, 5), (0.9, 20))  # 0.16 s < 0.30 s
    long_gap = frames((0.9, 20), (0.0, 20), (0.9, 20))  # 0.64 s
    assert len(regions(short_gap)) == 1
    assert len(regions(long_gap)) == 2


def test_blips_shorter_than_min_speech_are_dropped():
    probs = frames((0.0, 20), (0.9, 3), (0.0, 20))  # 96 ms of "speech"
    assert regions(probs) == []


def test_padding_is_clamped_to_the_file_and_merges_neighbours():
    probs = frames((0.9, 20), (0.0, 12), (0.9, 20))  # 0.384 s gap, padding 0.2 each side closes it
    out = regions(probs, VadParams(pad_s=0.2))
    assert out == [Region(0.0, round(52 * FRAME_S, 3))]


def test_energy_backend_finds_a_loud_burst_in_quiet_noise():
    rng = np.random.default_rng(0)
    quiet = rng.normal(0, 0.001, SAMPLE_RATE).astype(np.float32)
    t = np.arange(2 * SAMPLE_RATE) / SAMPLE_RATE
    loud = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    result = vad.detect_speech(np.concatenate([quiet, loud, quiet]), backend="energy")
    assert result.backend == "energy"
    assert len(result.regions) == 1
    assert result.regions[0].start == pytest.approx(0.9, abs=0.1)
    assert result.regions[0].end == pytest.approx(3.1, abs=0.1)


def test_digital_silence_is_never_speech():
    result = vad.detect_speech(np.zeros(3 * SAMPLE_RATE, dtype=np.float32), backend="energy")
    assert result.regions == []


def test_falls_back_to_energy_when_silero_is_unavailable(monkeypatch):
    def broken(_samples):
        raise ImportError("no native library for this platform")

    monkeypatch.setattr(vad, "silero_probabilities", broken)
    assert vad.detect_speech(np.zeros(SAMPLE_RATE, dtype=np.float32)).backend == "energy"


def test_quietest_point_lands_in_the_dip():
    probs = frames((0.9, 50), (0.1, 6), (0.9, 50))
    cut = vad.quietest_point(probs, lo=0.0, hi=106 * FRAME_S)
    assert 50 * FRAME_S <= cut <= 56 * FRAME_S
