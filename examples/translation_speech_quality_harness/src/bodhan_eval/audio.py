"""Audio helpers: decode, silence / clipping / duration metrics, and WAV perturbations
used by the error injector. WAV is handled natively; other formats go through ffmpeg."""
from __future__ import annotations

import io
import subprocess
import wave

import numpy as np


def decode(data: bytes) -> tuple[np.ndarray, int]:
    """Return mono float32 samples in [-1, 1] and sample rate."""
    try:
        return _decode_wav(data)
    except (wave.Error, EOFError):
        pass
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "wav", "-acodec", "pcm_s16le", "-ac", "1", "pipe:1"],
            input=data, capture_output=True, check=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        raise ValueError(f"cannot decode audio (not WAV and ffmpeg failed: {e})") from e
    return _decode_wav(proc.stdout)


def _decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise wave.Error(f"unsupported sample width {width}")
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    return x, sr


def encode_wav(x: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


def duration_seconds(data: bytes) -> float:
    try:
        x, sr = decode(data)
        return len(x) / sr
    except ValueError:
        return 0.0


def metrics(x: np.ndarray, sr: int, n_chars: int, silence_db: float = -45.0, win_ms: int = 20) -> dict:
    dur = len(x) / sr if sr else 0.0
    if len(x) == 0 or dur == 0:
        return {"duration_s": 0.0, "chars_per_s": None, "leading_silence_s": 0.0, "trailing_silence_s": 0.0,
                "max_internal_silence_s": 0.0, "silence_frac": 1.0, "clip_frac": 0.0, "empty": True}
    win = max(1, int(sr * win_ms / 1000))
    nwin = len(x) // win
    frames = x[: nwin * win].reshape(nwin, win) if nwin else x.reshape(1, -1)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    silent = 20 * np.log10(rms) < silence_db
    wsec = win / sr
    lead = trail = 0
    while lead < len(silent) and silent[lead]:
        lead += 1
    while trail < len(silent) - lead and silent[len(silent) - 1 - trail]:
        trail += 1
    longest = cur = 0
    for s in silent[lead: len(silent) - trail]:
        cur = cur + 1 if s else 0
        longest = max(longest, cur)
    return {
        "duration_s": round(dur, 3),
        "chars_per_s": round(n_chars / dur, 2) if dur else None,
        "leading_silence_s": round(lead * wsec, 3),
        "trailing_silence_s": round(trail * wsec, 3),
        "max_internal_silence_s": round(longest * wsec, 3),
        "silence_frac": round(float(silent.mean()), 4),
        "clip_frac": round(float((np.abs(x) >= 0.999).mean()), 5),
        "empty": False,
    }


# ---- perturbations (injector) ----
def truncate(x: np.ndarray, keep_frac: float) -> np.ndarray:
    return x[: max(1, int(len(x) * keep_frac))]


def repeat_segment(x: np.ndarray, sr: int, start_frac: float, seg_s: float) -> np.ndarray:
    a = int(len(x) * start_frac)
    b = min(len(x), a + int(seg_s * sr))
    return np.concatenate([x[:b], x[a:b], x[b:]])


def insert_silence(x: np.ndarray, sr: int, at_frac: float, seconds: float) -> np.ndarray:
    a = int(len(x) * at_frac)
    return np.concatenate([x[:a], np.zeros(int(seconds * sr), dtype=x.dtype), x[a:]])


def add_noise(x: np.ndarray, start_frac: float, end_frac: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = x.copy()
    a, b = int(len(x) * start_frac), int(len(x) * end_frac)
    y[a:b] = rng.uniform(-0.6, 0.6, size=b - a).astype(x.dtype)
    return y
