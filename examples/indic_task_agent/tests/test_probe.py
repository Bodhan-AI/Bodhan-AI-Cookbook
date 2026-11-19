"""probe_media on video: a silent audio track is flagged, a real one is not."""
from __future__ import annotations

import subprocess

import pytest

from bodhan_mcp.tools.probe import probe_media


def _video(path, audio_source: str):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
         "-f", "lavfi", "-i", audio_source, "-t", "1", "-shortest", str(path)],
        check=True,
    )
    return path


@pytest.mark.parametrize(
    ("audio", "warning"),
    [("anullsrc=r=16000:cl=mono", "silent_audio_track"), ("sine=frequency=440:duration=1", None)],
)
def test_video_audio_is_checked_for_silence(tmp_path, audio, warning):
    item = probe_media([str(_video(tmp_path / "clip.mp4", audio))]).items[0]
    assert item.has_audio is True
    assert (warning in item.warnings) if warning else ("silent_audio_track" not in item.warnings)
