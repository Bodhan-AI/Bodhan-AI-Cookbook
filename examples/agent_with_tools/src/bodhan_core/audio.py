"""WAV helpers (stdlib ``wave``; ffmpeg only for non-WAV input)."""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from pathlib import Path

from bodhan_core.errors import ToolError


def load_wav(path: Path) -> bytes | ToolError:
    """Return WAV bytes: the file itself if it is a WAV, else a 16 kHz mono conversion made with ffmpeg."""
    if path.suffix.lower() == ".wav":
        return path.read_bytes()
    if not shutil.which("ffmpeg"):
        return ToolError(code="ffmpeg_missing", message="Only .wav is supported without ffmpeg. Install ffmpeg to use other audio formats.")
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "wav", "-"], capture_output=True)
    if proc.returncode != 0:
        return ToolError(code="bad_audio", message=f"ffmpeg could not read the file: {proc.stderr.decode(errors='replace')[:200]}")
    return proc.stdout


def split_wav(wav_bytes: bytes, window_s: int) -> tuple[list[bytes], float]:
    """Cut WAV bytes into windows of at most ``window_s`` seconds. Returns (windows, total seconds)."""
    with wave.open(io.BytesIO(wav_bytes)) as src:
        params = src.getparams()
        rate = src.getframerate()
        windows = []
        while True:
            frames = src.readframes(rate * window_s)
            if not frames:
                break
            buf = io.BytesIO()
            with wave.open(buf, "wb") as dst:
                dst.setparams(params)
                dst.writeframes(frames)
            windows.append(buf.getvalue())
        return windows, params.nframes / rate


def join_wavs(clips: list[bytes]) -> tuple[bytes, float]:
    """Concatenate WAV clips that share one format. Returns (wav bytes, seconds)."""
    out = io.BytesIO()
    frames_total = 0
    rate = 1
    with wave.open(out, "wb") as dst:
        for i, clip in enumerate(clips):
            with wave.open(io.BytesIO(clip)) as src:
                if i == 0:
                    dst.setparams(src.getparams())
                    rate = src.getframerate()
                frames_total += src.getnframes()
                dst.writeframes(src.readframes(src.getnframes()))
    return out.getvalue(), frames_total / rate
