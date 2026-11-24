import shutil
import subprocess
import wave

import pytest

from bodhan_dub import media

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def tiny_video(tmp_path):
    """A 2-second test-pattern MP4 with a 440 Hz stereo tone, generated on the fly."""
    path = tmp_path / "tiny.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
         "-ac", "2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )
    return path


def test_probe_reads_duration_and_streams(tiny_video):
    info = media.probe(tiny_video)
    assert info.has_video and info.has_audio
    assert info.duration_s == pytest.approx(2.0, abs=0.1)


def test_extract_audio_is_mono_16k_pcm(tiny_video, tmp_path):
    wav = media.extract_audio(tiny_video, tmp_path / "a" / "out.wav")
    with wave.open(str(wav), "rb") as wf:
        assert (wf.getnchannels(), wf.getframerate(), wf.getsampwidth()) == (1, 16_000, 2)
        assert wf.getnframes() / wf.getframerate() == pytest.approx(2.0, abs=0.1)


def test_probe_raises_on_garbage(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(media.MediaError):
        media.probe(bad)
