"""Runs the audio helpers against real ffmpeg. Skipped when ffmpeg is not on PATH."""
import math
import shutil
import struct
import tempfile
import wave
from pathlib import Path

import pytest

from bodhan_mcp.audio_plan import detect_silences, extract_window, plan_windows, probe_duration


def _make_clip(path: Path, bursts=(10, 9, 11), gap=1.2, sr=16000):
    frames = bytearray()
    for burst in bursts:
        frames.extend(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / sr)))
                               for i in range(int(sr * burst))))
        frames.extend(b"\x00\x00" * int(sr * gap))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def test_plan_and_cut_real_audio():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "clip.wav"
        _make_clip(clip)
        duration = probe_duration(clip)
        assert duration == pytest.approx(33.6, rel=0.01)
        silences = detect_silences(clip, duration)
        assert len(silences) >= 3
        windows = plan_windows(duration, silences)
        assert all(e - s <= 25.0 for s, e in windows)
        out = extract_window(clip, Path(tmp) / "w0.wav", *windows[0])
        assert probe_duration(out) == pytest.approx(windows[0][1] - windows[0][0], abs=0.1)
