from __future__ import annotations

import numpy as np
import pytest

from bodhan_dub import media, mix
from bodhan_dub.errors import MediaError


def _ffmpeg_available() -> bool:
    try:
        media.ensure_tools()
    except MediaError:
        return False
    return True


RATE = media.TTS_SAMPLE_RATE


def _write(path, seconds: float, amplitude: float):
    samples = np.full(int(RATE * seconds), amplitude, dtype=np.float32)
    media.write_wav(path, samples, RATE)
    return path


def test_mix_adds_the_bed_under_the_dub(tmp_path) -> None:
    dub = _write(tmp_path / "dub.wav", 1.0, 0.2)
    bed = _write(tmp_path / "bed.wav", 1.0, 0.2)
    out = tmp_path / "mix.wav"

    info = mix.mix(dub, bed, out, bed_gain_db=-6.0)

    samples, _ = media.read_wav(out)
    expected = 0.2 + 0.2 * 10 ** (-6 / 20)
    assert samples[100] == pytest.approx(expected, abs=0.01)
    assert info["bed_gain_db"] == -6.0
    assert info["limited"] is False


def test_mix_limits_instead_of_clipping(tmp_path) -> None:
    dub = _write(tmp_path / "dub.wav", 0.5, 0.8)
    bed = _write(tmp_path / "bed.wav", 0.5, 0.8)
    out = tmp_path / "mix.wav"

    info = mix.mix(dub, bed, out, bed_gain_db=0.0)

    samples, _ = media.read_wav(out)
    assert info["limited"] is True
    assert np.max(np.abs(samples)) <= 0.991


def test_mix_pads_the_shorter_track(tmp_path) -> None:
    dub = _write(tmp_path / "dub.wav", 1.0, 0.1)
    bed = _write(tmp_path / "bed.wav", 2.0, 0.1)
    out = tmp_path / "mix.wav"

    info = mix.mix(dub, bed, out)

    assert info["duration_s"] == pytest.approx(2.0, abs=0.01)


def test_mix_rejects_mismatched_rates(tmp_path) -> None:
    dub = tmp_path / "dub.wav"
    media.write_wav(dub, np.zeros(8000, dtype=np.float32), 8000)
    bed = _write(tmp_path / "bed.wav", 1.0, 0.1)

    with pytest.raises(MediaError, match="sample rates"):
        mix.mix(dub, bed, tmp_path / "out.wav")


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
def test_loudness_is_measured(tmp_path) -> None:
    path = _write(tmp_path / "tone.wav", 2.0, 0.05)

    measured = mix.measure_loudness(path)

    assert measured["lufs"] is not None
    assert measured["lufs"] < 0


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
def test_normalisation_moves_towards_the_target(tmp_path) -> None:
    src = _write(tmp_path / "quiet.wav", 3.0, 0.02)
    dst = tmp_path / "loud.wav"

    before = mix.measure_loudness(src)["lufs"]
    result = mix.normalize_loudness(src, dst)
    after = mix.measure_loudness(dst)["lufs"]

    assert result["applied"] is True
    assert after is not None and before is not None
    assert abs(after - mix.TARGET_LUFS) < abs(before - mix.TARGET_LUFS)


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
def test_silence_is_copied_rather_than_failed(tmp_path) -> None:
    src = tmp_path / "silence.wav"
    media.write_wav(src, np.zeros(RATE, dtype=np.float32), RATE)
    dst = tmp_path / "out.wav"

    result = mix.normalize_loudness(src, dst)

    assert result["applied"] is False
    assert dst.exists()
