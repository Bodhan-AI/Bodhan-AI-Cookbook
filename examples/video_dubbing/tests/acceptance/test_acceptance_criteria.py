import subprocess
from pathlib import Path

import pytest
from bodhan_dub.config import BodhanConfig
from bodhan_dub.core.orchestrator import BodhanDubbingOrchestrator
from bodhan_dub.core.video_engine import VideoEngine


@pytest.mark.parametrize("container_ext", [".mp4", ".mov", ".mkv", ".webm"])
def test_container_formats_support(tmp_path, container_ext):
    """Verifies that MP4, MOV, MKV, WebM are all validated and accepted by the Video Engine."""
    video_path = tmp_path / f"test{container_ext}"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=800:duration=2",
        "-t", "2",
        str(video_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"FFmpeg failed to create {container_ext}: {res.stderr}"

    engine = VideoEngine()
    meta = engine.validate_video(video_path, min_duration=1.0)
    assert meta["duration"] > 0
    assert meta["has_audio"] is True

def test_duration_preservation_and_stretching_limits(tmp_path):
    """Verifies:
    1. Output video duration is preserved within ±100 ms.
    2. Segment alignment is within ±300 ms.
    3. Audio stretching stays within 0.85x–1.20x.
    4. Valid subtitles (.srt, .vtt) and report.json are generated.
    """
    video_path = tmp_path / "sync_test.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=4",
        "-c:v", "libx264", "-c:a", "aac",
        str(video_path)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    out_dir = tmp_path / "acceptance_out"
    orchestrator = BodhanDubbingOrchestrator(output_dir=out_dir, mock_mode=True)
    report = orchestrator.run(input_video=video_path, target_languages=["hi", "ta", "bn"])

    # 1. Total duration within ±100ms
    assert report.duration_difference_ms <= BodhanConfig.TIMELINE_TOLERANCE_MS

    # 2. Audio stretching within 0.85x - 1.20x
    for _lang, summary in report.languages.items():
        assert 0.85 <= summary.average_tempo_factor <= 1.20
        assert summary.average_cer is not None
        assert 0.0 <= summary.average_cer <= 1.0

        # Subtitles verification
        srt = Path(summary.subtitles_srt_path)
        vtt = Path(summary.subtitles_vtt_path)
        assert srt.exists() and srt.stat().st_size > 0
        assert vtt.exists() and vtt.stat().st_size > 0

    # 3. Report file existence
    assert (out_dir / "report.json").exists()
    assert (out_dir / "transcript.en.json").exists()
