import subprocess

import pytest
from bodhan_dub.core.orchestrator import BodhanDubbingOrchestrator


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("video_data")
    video_path = tmp_dir / "sample.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(video_path)
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return video_path

def test_full_pipeline_mock(sample_video, tmp_path):
    out_dir = tmp_path / "dub_out"
    orchestrator = BodhanDubbingOrchestrator(
        output_dir=out_dir,
        mock_mode=True
    )
    report = orchestrator.run(
        input_video=sample_video,
        target_languages=["hi", "ta", "bn"]
    )

    assert report.overall_status == "SUCCESS"
    assert report.duration_within_spec is True
    assert (out_dir / "report.json").exists()
    assert (out_dir / "transcript.en.json").exists()

    for lang in ["hi", "ta", "bn"]:
        lang_dir = out_dir / lang
        assert (lang_dir / f"video.{lang}.mp4").exists()
        assert (lang_dir / f"subtitles.{lang}.srt").exists()
        assert (lang_dir / f"subtitles.{lang}.vtt").exists()
        assert (lang_dir / f"segments.{lang}.json").exists()
