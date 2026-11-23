from __future__ import annotations

import subprocess

import numpy as np
import pytest

from bodhan_dub import media, mux
from bodhan_dub.errors import MediaError


def _ffmpeg_available() -> bool:
    try:
        media.ensure_tools()
    except MediaError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")


def _clip(tmp_path, seconds: float = 3.0):
    audio = tmp_path / "a.wav"
    media.write_wav(audio, np.zeros(int(media.TTS_SAMPLE_RATE * seconds), dtype=np.float32), media.TTS_SAMPLE_RATE)
    video = tmp_path / "in.mp4"
    subprocess.run(
        [
            media.find_binary("ffmpeg"),
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=160x120:r=10:d={int(seconds)}",
            "-i",
            str(audio),
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    return video, audio


def test_mux_copies_the_video_stream(tmp_path) -> None:
    video, audio = _clip(tmp_path)
    out = tmp_path / "out.mp4"

    info = mux.mux(video, audio, out, duration_s=3.0)

    assert out.exists()
    assert info["video"] == "copy"
    probed = media.probe(out)
    assert probed.has_video and probed.has_audio


def test_mux_trims_to_the_requested_duration(tmp_path) -> None:
    video, audio = _clip(tmp_path, seconds=4.0)
    out = tmp_path / "out.mp4"

    mux.mux(video, audio, out, duration_s=2.5)

    probed = media.probe(out)
    assert probed.duration_s == pytest.approx(2.5, abs=0.15)


def test_verify_duration_reports_the_delta(tmp_path) -> None:
    video, audio = _clip(tmp_path)
    out = tmp_path / "out.mp4"
    mux.mux(video, audio, out, duration_s=3.0)

    result = mux.verify_duration(out, 3.0)

    assert result["within_tolerance"] is True
    assert abs(result["delta_ms"]) <= 100


def test_verify_duration_flags_a_mismatch(tmp_path) -> None:
    video, audio = _clip(tmp_path)
    out = tmp_path / "out.mp4"
    mux.mux(video, audio, out, duration_s=3.0)

    result = mux.verify_duration(out, 10.0)

    assert result["within_tolerance"] is False
