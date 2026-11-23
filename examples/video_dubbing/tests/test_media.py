from __future__ import annotations

import subprocess

import numpy as np
import pytest

from bodhan_dub import media
from bodhan_dub.errors import MediaError
from tests.conftest import SR, build_signal


def _ffmpeg_available() -> bool:
    try:
        media.ensure_tools()
    except MediaError:
        return False
    return True


requires_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe not installed")


def test_wav_roundtrip_preserves_samples(tmp_path) -> None:
    samples = build_signal([(0.5, 1.5)], total_s=2.0)
    path = tmp_path / "a.wav"
    media.write_wav(path, samples, SR)

    loaded, rate = media.read_wav(path)

    assert rate == SR
    assert len(loaded) == len(samples)
    assert np.max(np.abs(loaded - samples)) < 1e-4


def test_to_wav_bytes_is_a_valid_wav() -> None:
    payload = media.to_wav_bytes(build_signal([(0.1, 0.4)], total_s=0.5), SR)
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WAVE"
    assert len(payload) > 44


def test_slice_is_sample_exact(sr: int) -> None:
    samples = np.arange(sr * 4, dtype=np.float32)
    cut = media.slice_samples(samples, sr, 1.0, 2.0)

    assert len(cut) == sr
    assert cut[0] == samples[sr]
    assert cut[-1] == samples[2 * sr - 1]


def test_slice_clamps_out_of_range(sr: int) -> None:
    samples = np.arange(sr, dtype=np.float32)

    assert len(media.slice_samples(samples, sr, -5.0, 0.5)) == sr // 2
    assert len(media.slice_samples(samples, sr, 0.5, 99.0)) == sr // 2
    assert len(media.slice_samples(samples, sr, 2.0, 1.0)) == 0


def test_missing_file_raises_actionable_error(tmp_path) -> None:
    with pytest.raises(MediaError, match="does not exist"):
        media.probe(tmp_path / "nope.mp4")


def test_empty_file_raises(tmp_path) -> None:
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(MediaError, match="empty"):
        media.probe(empty)


@requires_ffmpeg
def test_probe_reads_duration_and_streams(tmp_path) -> None:
    path = _make_video(tmp_path)
    info = media.probe(path)

    assert info.duration_s == pytest.approx(3.0, abs=0.4)
    assert info.has_video is True
    assert info.has_audio is True


@requires_ffmpeg
def test_extract_produces_16k_mono(tmp_path) -> None:
    path = _make_video(tmp_path)
    out = tmp_path / "audio.wav"
    media.extract_pcm_wav(path, out)

    samples, rate = media.read_wav(out)

    assert rate == 16_000
    assert samples.ndim == 1
    assert len(samples) > 0


def test_file_digest_is_stable_and_content_sensitive(tmp_path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello")
    b.write_bytes(b"hello")

    assert media.file_digest(a) == media.file_digest(b)
    b.write_bytes(b"hello!")
    assert media.file_digest(a) != media.file_digest(b)


def _make_video(tmp_path):
    wav = tmp_path / "a.wav"
    media.write_wav(wav, build_signal([(0.2, 2.8)], total_s=3.0), SR)
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            media.find_binary("ffmpeg"),
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x120:r=10:d=3",
            "-i",
            str(wav),
            "-c:v",
            "mpeg4",
            "-q:v",
            "6",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
