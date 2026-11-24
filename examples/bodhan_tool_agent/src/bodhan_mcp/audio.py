"""Audio helpers: decoding, silence-aware chunking for the 30 s STT window, joining TTS output, encoding."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .errors import ToolError

STT_RATE = 16000
FRAME_S = 0.02


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Decode to mono float32. libsndfile handles wav/flac/ogg/mp3; anything else (m4a, webm, video) needs ffmpeg."""
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception:
        data, sr = _decode_with_ffmpeg(path)
    return data.mean(axis=1), sr


def _decode_with_ffmpeg(path: Path) -> tuple[np.ndarray, int]:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise ToolError(
            "unsupported_audio_format",
            f"Cannot decode '{path.name}': not WAV/FLAC/OGG/MP3 and ffmpeg is not installed.",
            "Install ffmpeg to read M4A/AAC/WebM/video, or provide a WAV/MP3/FLAC/OGG file.",
        )
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "a.wav"
        r = subprocess.run([ff, "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", str(STT_RATE), str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            raise ToolError("audio_decode_failed", f"ffmpeg could not read '{path.name}': {r.stderr.strip()[:200]}",
                            "The file may be corrupt or have no audio stream.")
        data, sr = sf.read(str(out), dtype="float32", always_2d=True)
    return data, sr


def resample(x: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target or len(x) == 0:
        return x
    n = int(round(len(x) * target / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def to_wav_bytes(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def from_wav_bytes(b: bytes) -> tuple[np.ndarray, int]:
    data, sr = sf.read(io.BytesIO(b), dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


def frame_rms(x: np.ndarray, sr: int) -> np.ndarray:
    n = max(int(sr * FRAME_S), 1)
    usable = (len(x) // n) * n
    if usable == 0:
        return np.zeros(1, dtype=np.float32)
    return np.sqrt((x[:usable].reshape(-1, n) ** 2).mean(axis=1))


def split_for_stt(x: np.ndarray, sr: int, max_s: float = 28.0, search_s: float = 8.0) -> list[tuple[int, int]]:
    """Cut points for windows no longer than max_s. For every cut we look at the last `search_s` seconds of the window
    and cut at the quietest ~100 ms, so we split in pauses rather than in the middle of a word."""
    total = len(x)
    max_n = int(max_s * sr)
    if total <= max_n:
        return [(0, total)]
    rms = frame_rms(x, sr)
    fn = int(sr * FRAME_S)
    smooth = np.convolve(rms, np.ones(5) / 5, mode="same")
    spans: list[tuple[int, int]] = []
    pos = 0
    while total - pos > max_n:
        lo_f = (pos + max_n - int(search_s * sr)) // fn
        hi_f = (pos + max_n) // fn
        lo_f, hi_f = max(lo_f, pos // fn + 1), min(hi_f, len(smooth) - 1)
        cut = (lo_f + int(np.argmin(smooth[lo_f:hi_f]))) * fn if hi_f > lo_f else pos + max_n
        cut = min(max(cut, pos + sr), pos + max_n)
        spans.append((pos, cut))
        pos = cut
    spans.append((pos, total))
    return spans


def is_silent(x: np.ndarray, threshold: float = 0.003) -> bool:
    return len(x) == 0 or float(np.sqrt((x ** 2).mean())) < threshold


def stats(x: np.ndarray, sr: int) -> dict:
    dur = len(x) / sr if sr else 0.0
    rms = frame_rms(x, sr)
    voiced = float((rms > 0.01).mean()) if len(rms) else 0.0
    return {
        "duration_s": round(dur, 2),
        "peak": round(float(np.abs(x).max()) if len(x) else 0.0, 3),
        "rms": round(float(np.sqrt((x ** 2).mean())) if len(x) else 0.0, 4),
        "voiced_ratio": round(voiced, 2),
        "clipping": bool(len(x) and (np.abs(x) > 0.999).mean() > 0.001),
    }


def join(parts: list[tuple[np.ndarray, int]], gap_s: float = 0.3) -> tuple[np.ndarray, int]:
    if not parts:
        raise ToolError("no_audio", "Nothing to join.")
    sr = parts[0][1]
    gap = np.zeros(int(gap_s * sr), dtype=np.float32)
    out: list[np.ndarray] = []
    for i, (x, s) in enumerate(parts):
        out.append(resample(x, s, sr))
        if i < len(parts) - 1:
            out.append(gap)
    return np.concatenate(out), sr


def write_audio(path: Path, x: np.ndarray, sr: int) -> str:
    """Write by extension. MP3 uses libsndfile; if that build lacks it, fall back to WAV and say so."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower().lstrip(".")
    fmt = {"wav": "WAV", "mp3": "MP3", "flac": "FLAC", "ogg": "OGG"}.get(ext)
    if fmt is None:
        raise ToolError("unsupported_format", f"Cannot write .{ext} audio.", "Use .wav, .mp3, .flac or .ogg.")
    if fmt == "MP3" and "MP3" not in sf.available_formats():
        path = path.with_suffix(".wav")
        sf.write(str(path), x, sr, format="WAV", subtype="PCM_16")
        return str(path)
    sf.write(str(path), x, sr, format=fmt)
    return str(path)
