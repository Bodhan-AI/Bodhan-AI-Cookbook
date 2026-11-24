"""Duration-fit ladder: pad -> stretch -> condense -> overflow, speed always clamped."""

import pytest

from bodhan_dub.pipeline.fit import clamp_speed, plan_fit, sync_offset_ms


def test_shorter_audio_pads_at_natural_speed():
    p = plan_fit(5.0, 4.0)
    assert p.action == "pad"
    assert p.speed == 1.0
    assert p.pad_s == pytest.approx(1.0)
    assert p.fits


def test_mild_overrun_stretches_to_fit():
    p = plan_fit(5.0, 5.5)
    assert p.action == "stretch"
    assert 1.0 < p.speed <= 1.2
    assert p.predicted_dur == pytest.approx(5.0, abs=1e-3)
    assert p.fits


def test_stretch_never_exceeds_max_band():
    p = plan_fit(5.0, 6.0)  # needs exactly 1.2x
    assert p.action == "stretch"
    assert p.speed == pytest.approx(1.2, abs=1e-3)


def test_large_overrun_triggers_condense():
    p = plan_fit(5.0, 10.0, can_condense=True)
    assert p.action == "condense"
    assert p.condense_ratio == pytest.approx(0.6, abs=1e-6)  # 1.2 * 5 / 10
    assert not p.fits


def test_condense_exhausted_falls_back_to_overflow():
    p = plan_fit(5.0, 10.0, can_condense=False)
    assert p.action == "overflow"
    assert p.speed == pytest.approx(1.2)
    assert not p.fits


def test_clamp_speed_bounds():
    assert clamp_speed(2.0, 0.85, 1.2) == 1.2
    assert clamp_speed(0.5, 0.85, 1.2) == 0.85
    assert clamp_speed(1.0, 0.85, 1.2) == 1.0


def test_sync_offset_sign():
    assert sync_offset_ms(1.0, 1.1) == pytest.approx(100.0)
    assert sync_offset_ms(1.0, 0.95) == pytest.approx(-50.0)
