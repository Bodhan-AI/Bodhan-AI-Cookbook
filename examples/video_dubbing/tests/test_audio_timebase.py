"""Timebase: audio that starts later than the video must land at the right container time."""

import subprocess

import numpy as np
import soundfile as sf

from bodhan_dub.audio import extract_audio, probe_media


def _make_video(path, audio_delay):
    # 3 s black video; a 1 s beep that starts `audio_delay` seconds into the file
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=3",
                    "-itsoffset", str(audio_delay), "-f", "lavfi", "-i", "sine=frequency=800:sample_rate=16000:duration=1",
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "pcm_s16le", str(path)], check=True)


def _onset(wav, sr):
    return np.where(np.abs(wav) > 0.05)[0][0] / sr


def test_probe_reports_audio_offset(tmp_path):
    v = tmp_path / "v.mkv"
    _make_video(v, 0.5)
    m = probe_media(v)
    assert m["has_audio"] and m["has_video"]
    assert abs(m["audio_offset"] - 0.5) < 0.02


def test_extracted_audio_is_on_container_timeline(tmp_path):
    v = tmp_path / "v.mkv"
    _make_video(v, 0.5)
    out = extract_audio(v, tmp_path / "a.wav")
    wav, sr = sf.read(out)
    assert abs(_onset(wav, sr) - 0.5) < 0.02          # beep at 0.5 s of the file, not at 0
    assert abs(len(wav) / sr - probe_media(v)["duration"]) < 0.02


def test_extract_without_offset_and_stereo(tmp_path):
    v = tmp_path / "v.mkv"
    _make_video(v, 0.0)
    wav, sr = sf.read(extract_audio(v, tmp_path / "s.wav", sample_rate=44100, channels=2, duration=2.0))
    assert wav.shape[1] == 2 and abs(len(wav) / sr - 2.0) < 0.01
    assert _onset(wav[:, 0], sr) < 0.02
