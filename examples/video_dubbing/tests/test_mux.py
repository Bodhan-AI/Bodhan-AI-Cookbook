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


def _libass() -> bool:
    try:
        return media.has_filter("subtitles")
    except MediaError:
        return False


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


def test_mux_embeds_a_selectable_subtitle_track(tmp_path) -> None:
    video, audio = _clip(tmp_path)
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nनमस्ते\n", encoding="utf-8")
    out = tmp_path / "out.mp4"

    info = mux.mux(
        video, audio, out, duration_s=3.0, subtitle_path=srt, subtitle_language="hin"
    )

    assert info["subtitles"] == "embedded"
    assert info["video"] == "copy"
    assert mux.has_subtitle_track(out) is True


def test_no_subtitle_track_when_not_requested(tmp_path) -> None:
    video, audio = _clip(tmp_path)
    out = tmp_path / "out.mp4"

    info = mux.mux(video, audio, out, duration_s=3.0)

    assert info["subtitles"] == "none"
    assert mux.has_subtitle_track(out) is False


def test_burn_subtitles_without_libass_fails_with_an_explanation(tmp_path) -> None:
    if _libass():
        pytest.skip("this ffmpeg build has libass; the failure path cannot be exercised")
    video, audio = _clip(tmp_path)
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n", encoding="utf-8")

    with pytest.raises(MediaError, match="libass"):
        mux.mux(video, audio, tmp_path / "out.mp4", duration_s=3.0, subtitle_path=srt, burn_subtitles=True)


def test_burn_subtitles_requires_a_subtitle_file(tmp_path) -> None:
    video, audio = _clip(tmp_path)

    with pytest.raises(MediaError, match="needs a subtitle file"):
        mux.mux(video, audio, tmp_path / "out.mp4", duration_s=3.0, burn_subtitles=True)
