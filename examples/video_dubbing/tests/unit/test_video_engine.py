
import pytest
from bodhan_dub.core.video_engine import VideoEngine, VideoValidationError


def test_supported_extensions():
    engine = VideoEngine()
    assert ".mp4" in engine.SUPPORTED_EXTENSIONS
    assert ".mov" in engine.SUPPORTED_EXTENSIONS
    assert ".mkv" in engine.SUPPORTED_EXTENSIONS
    assert ".webm" in engine.SUPPORTED_EXTENSIONS

def test_unsupported_extension_rejection(tmp_path):
    engine = VideoEngine()
    invalid_file = tmp_path / "video.avi"
    invalid_file.touch()
    with pytest.raises(VideoValidationError, match="Unsupported video format"):
        engine.validate_video(invalid_file)

def test_video_without_audio_extraction(tmp_path):
    engine = VideoEngine()
    dummy_video = tmp_path / "silent.mp4"
    dummy_video.touch()
    speech_path = tmp_path / "speech.wav"
    hq_path = tmp_path / "hq.wav"

    s_out, h_out = engine.extract_audio(
        dummy_video,
        speech_path,
        hq_path,
        has_audio=False,
        duration=2.0
    )
    assert s_out.exists()
    assert h_out.exists()
    import wave
    with wave.open(str(s_out), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 32000
