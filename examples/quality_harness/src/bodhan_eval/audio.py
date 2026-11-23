"""Audio I/O and signal measurements for the speech checks (plan §12.1). numpy + stdlib wave; ffmpeg only
to decode formats other than PCM WAV.

Frames are 25 ms. A frame is silent when its RMS is more than SILENCE_DB below the clip's loudest
frame, so the measure doesn't depend on recording level.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FRAME_S = 0.025
SILENCE_DB = 40.0  # below the loudest frame
CLIP_LEVEL = 0.999  # of full scale


@dataclass
class Audio:
    samples: np.ndarray  # float32 mono in [-1, 1]
    rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.rate


def load(path: Path) -> Audio:
    """Any audio file as mono float32. PCM WAV directly, anything else through ffmpeg (16 kHz)."""
    try:
        with wave.open(str(path), "rb") as w:
            width, channels, rate, n = w.getsampwidth(), w.getnchannels(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if width not in (1, 2, 4):
            raise wave.Error(f"unsupported sample width {width}")
    except (wave.Error, EOFError):
        return _ffmpeg(path)
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    x = (x - 128) / 128 if width == 1 else x / float(2 ** (8 * width - 1))
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return Audio(x, rate)


def _ffmpeg(path: Path) -> Audio:
    if not shutil.which("ffmpeg"):
        raise ValueError(f"{path.name}: not PCM WAV and ffmpeg is not installed")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "wav", "-ac", "1", "-ar", "16000",
                        "-acodec", "pcm_s16le", "-"], capture_output=True, check=False)
    if r.returncode != 0 or not r.stdout:
        raise ValueError(f"{path.name}: ffmpeg could not decode it: {r.stderr.decode(errors='replace')[:200]}")
    with wave.open(io.BytesIO(r.stdout), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        return Audio(x, w.getframerate())


def save(audio: Audio, path: Path) -> Path:
    """16-bit PCM WAV. Byte-identical for identical samples, so transcripts of it cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio.samples, -1, 1) * 32767).round().astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.rate)
        w.writeframes(pcm)
    return path


def frame_rms(audio: Audio) -> np.ndarray:
    n = max(1, int(FRAME_S * audio.rate))
    k = len(audio.samples) // n
    if k == 0:
        return np.zeros(0, dtype=np.float32)
    f = audio.samples[: k * n].reshape(k, n)
    return np.sqrt((f ** 2).mean(axis=1))


def silent_frames(audio: Audio) -> np.ndarray:
    rms = frame_rms(audio)
    if len(rms) == 0 or rms.max() == 0:
        return np.ones(len(rms), dtype=bool)
    return rms < rms.max() * 10 ** (-SILENCE_DB / 20)


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) frame indices of each run of True."""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def silences(audio: Audio, min_s: float = 0.0) -> list[tuple[float, float]]:
    """(start, end) seconds of each silent stretch at least min_s long."""
    return [(s * FRAME_S, e * FRAME_S) for s, e in runs(silent_frames(audio)) if (e - s) * FRAME_S >= min_s]


def clipped_fraction(audio: Audio) -> float:
    """Share of samples in runs of >= 3 at full scale (a single peak at full scale is not clipping)."""
    hot = np.abs(audio.samples) >= CLIP_LEVEL
    if not hot.any():
        return 0.0
    return sum(e - s for s, e in runs(hot) if e - s >= 3) / len(audio.samples)


def flatness(audio: Audio) -> np.ndarray:
    """Spectral flatness per frame (geometric / arithmetic mean of the power spectrum): ~0 for tones and
    voiced speech, towards 1 for white noise."""
    n = max(1, int(FRAME_S * audio.rate))
    k = len(audio.samples) // n
    if k == 0:
        return np.zeros(0, dtype=np.float32)
    f = audio.samples[: k * n].reshape(k, n) * np.hanning(n)
    p = np.abs(np.fft.rfft(f, axis=1)) ** 2 + 1e-12
    return np.exp(np.log(p).mean(axis=1)) / p.mean(axis=1)


def noise_level(audio: Audio, window_s: float = 0.5) -> np.ndarray:
    """Per frame, the median flatness over a window around it, silent frames counted as 0 (digital
    silence is "flat" too). Speech frames swing between tonal and hissy; a noise burst stays flat."""
    f = flatness(audio)
    f[silent_frames(audio)[: len(f)]] = 0.0
    half = max(1, int(window_s / FRAME_S) // 2)
    return np.array([np.median(f[max(0, i - half):i + half]) for i in range(len(f))], dtype=np.float32)


def cut(audio: Audio, start_s: float, end_s: float) -> Audio:
    a, b = int(start_s * audio.rate), int(end_s * audio.rate)
    return Audio(audio.samples[a:b], audio.rate)


def concat(*parts: Audio) -> Audio:
    return Audio(np.concatenate([p.samples for p in parts]).astype(np.float32), parts[0].rate)


def pieces(audio: Audio, max_s: float = 25.0, min_s: float = 10.0, slack: float = 3.0) -> list[tuple[float, float]]:
    """Split points for transcription (Indic-Transcribe takes <= 30 s): at the longest pause between
    min_s and max_s into each piece, else a hard cut at max_s. A last piece up to max_s + slack stays whole."""
    out, t = [], 0.0
    gaps = silences(audio, 0.1)
    while audio.duration - t > max_s + slack:
        inside = [(e - s, (s + e) / 2) for s, e in gaps if t + min_s <= (s + e) / 2 <= t + max_s]
        cut_at = max(inside)[1] if inside else t + max_s
        out.append((t, cut_at))
        t = cut_at
    out.append((t, audio.duration))
    return out
