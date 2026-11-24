"""Audio helpers: decode to mono float32, encode PCM16 WAV, concatenate, and convert via ffmpeg."""
from __future__ import annotations

import io
import shutil
import subprocess
import wave

import numpy as np


def is_wav(b: bytes) -> bool:
    return len(b) > 12 and b[:4] == b"RIFF" and b[8:12] == b"WAVE"


def ensure_wav(b: bytes) -> bytes:
    """Return WAV bytes; anything else (mp3, flac, ogg) is converted with ffmpeg if available."""
    if is_wav(b):
        return b
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("audio is not WAV and ffmpeg is not installed")
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "wav", "-acodec", "pcm_s16le", "-ac", "1", "pipe:1"],
                          input=b, capture_output=True, check=True)
    return proc.stdout


def decode(b: bytes) -> tuple[np.ndarray, int]:
    """WAV bytes -> (mono float32 in [-1, 1], sample_rate)."""
    b = ensure_wav(b)
    with wave.open(io.BytesIO(b)) as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def encode(x: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def wav_duration_s(b: bytes) -> float:
    if is_wav(b):
        with wave.open(io.BytesIO(b)) as w:
            return w.getnframes() / float(w.getframerate())
    x, sr = decode(b)
    return len(x) / sr


def concat(chunks: list[np.ndarray], sr: int, gap_s: float = 0.25) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Join chunks with short silences; return audio and each chunk's (start_s, end_s)."""
    gap = np.zeros(int(gap_s * sr), dtype=np.float32)
    parts, times, t = [], [], 0.0
    for i, c in enumerate(chunks):
        if i:
            parts.append(gap)
            t += len(gap) / sr
        parts.append(c.astype(np.float32))
        times.append((round(t, 3), round(t + len(c) / sr, 3)))
        t += len(c) / sr
    return (np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)), times


def split_for_asr(x: np.ndarray, sr: int, max_s: float = 25.0) -> list[tuple[int, int]]:
    """Split long audio at the quietest point near each max_s boundary (ASR accepts ~30 s)."""
    n, hop, out, start = len(x), int(0.02 * sr), [], 0
    limit = int(max_s * sr)
    while n - start > limit:
        lo, hi = start + int(limit * 0.6), start + limit
        seg = x[lo:hi]
        frames = max(1, len(seg) // hop)
        energy = np.array([np.sqrt(np.mean(seg[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(frames)])
        cut = lo + int(np.argmin(energy)) * hop
        out.append((start, cut))
        start = cut
    out.append((start, n))
    return out
