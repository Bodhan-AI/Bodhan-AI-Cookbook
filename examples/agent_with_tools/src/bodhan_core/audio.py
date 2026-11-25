"""WAV helpers (stdlib ``wave``; ffmpeg only for non-WAV input)."""

from __future__ import annotations

import io
import shutil
from array import array
import subprocess
import wave
from pathlib import Path

from bodhan_core.errors import ToolError


def _is_plain_pcm_wav(data: bytes) -> bool:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() > 0 and w.getframerate() > 0
    except (wave.Error, EOFError):
        return False


def load_wav(path: Path) -> bytes | ToolError:
    """Return WAV bytes: the file itself if it is a readable PCM WAV, else a 16 kHz mono conversion made with ffmpeg."""
    if path.suffix.lower() == ".wav":
        data = path.read_bytes()
        if _is_plain_pcm_wav(data) or not shutil.which("ffmpeg"):  # float/ADPCM WAVs need ffmpeg when it is available
            return data
    if not shutil.which("ffmpeg"):
        return ToolError(code="ffmpeg_missing", message="Only .wav is supported without ffmpeg. Install ffmpeg to use other audio formats.")
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "wav", "-"], capture_output=True)
    if proc.returncode != 0:
        return ToolError(code="bad_audio", message=f"ffmpeg could not read the file: {proc.stderr.decode(errors='replace')[:200]}")
    return proc.stdout


SEARCH_S = 5.0  # a cut is placed at the quietest spot in the last SEARCH_S seconds of a window
_FRAME_S = 0.02


def _quietest_cut(frames: bytes, width: int, channels: int, rate: int, limit: int) -> int:
    """Frame index (<= ``limit``) of the quietest 20 ms inside the last SEARCH_S seconds before ``limit``.

    Cutting in a pause instead of at a fixed second keeps words whole, which matters because each window is
    transcribed on its own. Only 16-bit PCM is analysed; anything else is cut at ``limit``. Ties pick the latest spot.
    """
    if width != 2:
        return limit
    step = max(int(rate * _FRAME_S), 1)
    first = max(limit - int(rate * SEARCH_S), step)
    samples = array("h")
    samples.frombytes(frames[: limit * width * channels])
    best_pos, best_energy = limit, None
    for pos in range(limit - step, first - 1, -step):  # latest first, so ties keep the latest
        chunk = samples[pos * channels : (pos + step) * channels : channels]
        energy = sum(abs(s) for s in chunk)
        if best_energy is None or energy < best_energy:
            best_pos, best_energy = pos + step, energy  # cut at the end of the quiet frame
    return best_pos


def split_wav_timed(wav_bytes: bytes, window_s: int) -> tuple[list[tuple[float, bytes]], float]:
    """Cut WAV bytes into windows of at most ``window_s`` seconds, preferably at pauses.

    Returns ([(start second, window WAV bytes)], total seconds).
    """
    with wave.open(io.BytesIO(wav_bytes)) as src:
        params = src.getparams()
        rate, width, channels = src.getframerate(), src.getsampwidth(), src.getnchannels()
        windows: list[tuple[float, bytes]] = []
        start = 0
        while True:
            frames = src.readframes(rate * window_s)
            if not frames:
                break
            n = len(frames) // (width * channels)
            if n == rate * window_s and src.tell() < src.getnframes():  # a full window with audio still to come: move the cut to a pause
                cut = _quietest_cut(frames, width, channels, rate, n)
                src.setpos(src.tell() - (n - cut))
                frames, n = frames[: cut * width * channels], cut
            buf = io.BytesIO()
            with wave.open(buf, "wb") as dst:
                dst.setparams(params)
                dst.writeframes(frames)
            windows.append((start / rate, buf.getvalue()))
            start += n
        return windows, params.nframes / rate


def split_wav(wav_bytes: bytes, window_s: int) -> tuple[list[bytes], float]:
    """Like ``split_wav_timed`` but returns only the window bytes. Returns (windows, total seconds)."""
    timed, total = split_wav_timed(wav_bytes, window_s)
    return [w for _, w in timed], total


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
