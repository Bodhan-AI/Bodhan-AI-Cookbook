"""Audio plumbing in plain code: decode, window, snap cuts to silence, encode, concatenate.

ffmpeg is used when installed (any container/codec, video too); otherwise PCM WAV is read with the stdlib.
"""

from __future__ import annotations

import array
import io
import shutil
import subprocess
import wave
from pathlib import Path

from .errors import ToolError

TARGET_RATE = 16_000


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_duration(path: Path) -> float | None:
    """Duration in seconds, or None if it cannot be determined."""
    if shutil.which("ffprobe"):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            return float(r.stdout.strip())
        except (ValueError, subprocess.SubprocessError, OSError):
            pass
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except (wave.Error, EOFError, OSError):
        return None


def has_audio_stream(path: Path) -> bool | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None  # unreadable: let the decoder report it
        return "audio" in r.stdout
    except (subprocess.SubprocessError, OSError):
        return None


def decode_pcm16(path: Path) -> tuple[array.array, int]:
    """Decode any audio/video file to mono 16-bit PCM at 16 kHz."""
    if have_ffmpeg():
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(TARGET_RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=600,
        )
        if r.returncode != 0 or not r.stdout:
            raise ToolError("undecodable_audio", f"Could not decode audio from {path.name}: {r.stderr.decode(errors='ignore')[:200]}",
                            "The file may be corrupt or have no audio track.")
        samples = array.array("h")
        samples.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
        return samples, TARGET_RATE
    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2:
                raise ToolError("unsupported_format", "Only 16-bit PCM WAV is supported without ffmpeg.", "Install ffmpeg or convert to 16-bit WAV.")
            raw, ch, rate = wf.readframes(wf.getnframes()), wf.getnchannels(), wf.getframerate()
    except (wave.Error, EOFError) as e:
        raise ToolError("unsupported_format", f"Cannot read {path.name} without ffmpeg ({e}).", "Install ffmpeg, or provide a PCM WAV file.") from e
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    if ch > 1:
        samples = array.array("h", (sum(samples[i : i + ch]) // ch for i in range(0, len(samples) - ch + 1, ch)))
    return samples, rate


def plan_windows(samples: array.array, rate: int, max_s: float, snap_s: float) -> list[tuple[int, int]]:
    """Split into windows of at most max_s seconds, cutting at the quietest ~50 ms frame in the last snap_s seconds."""
    n, max_n = len(samples), int(max_s * rate)
    if n <= max_n:
        return [(0, n)] if n else []
    frame = max(int(0.05 * rate), 1)
    windows, start = [], 0
    while start < n:
        end = min(start + max_n, n)
        if end < n:
            lo = max(end - int(snap_s * rate), start + frame)
            best, best_e = end, None
            for pos in range(lo, end - frame, frame):
                e = sum(abs(s) for s in samples[pos : pos + frame])
                if best_e is None or e < best_e:
                    best, best_e = pos + frame // 2, e
            end = max(min(best, end), start + frame)
        windows.append((start, end))
        start = end
    return windows


def pcm_to_wav_bytes(samples: array.array, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def wav_bytes_to_pcm(data: bytes) -> tuple[array.array, int]:
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            if wf.getsampwidth() != 2:
                raise ToolError("bad_audio_from_model", "Indic-Speak returned non 16-bit audio.", "Retry later.", True)
            raw, ch, rate = wf.readframes(wf.getnframes()), wf.getnchannels(), wf.getframerate()
    except (wave.Error, EOFError) as e:
        raise ToolError("bad_audio_from_model", f"Indic-Speak did not return a valid WAV ({e}).", "Retry later.", True) from e
    s = array.array("h")
    s.frombytes(raw[: len(raw) // 2 * 2])
    if ch > 1:
        s = array.array("h", (sum(s[i : i + ch]) // ch for i in range(0, len(s) - ch + 1, ch)))
    return s, rate


def encode_with_metadata(wav_path: Path, out_path: Path, comment: str) -> bool:
    """Re-encode (wav -> mp3 / wav with tags) via ffmpeg, embedding an AI-generated label. False if no ffmpeg."""
    if not have_ffmpeg():
        return False
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(wav_path), "-metadata", f"comment={comment}", "-metadata", "title=AI-generated speech", str(out_path)],
        capture_output=True, timeout=300,
    )
    return r.returncode == 0 and out_path.exists()
