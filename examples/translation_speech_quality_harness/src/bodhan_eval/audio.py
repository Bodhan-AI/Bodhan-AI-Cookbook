"""audio_io: Minimal audio I/O helpers (no ffmpeg needed for WAV/FLAC/OGG).
audio_analysis: Acoustic analysis with numpy only (no models, no ffmpeg).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import soundfile as sf


# -------------------- audio_io
def audio_duration(data: bytes) -> float:
    info = sf.info(io.BytesIO(data))
    return float(info.frames) / float(info.samplerate)


def read_audio(data: bytes) -> tuple[np.ndarray, int]:
    """Return (mono float32 samples in [-1, 1], sample_rate)."""
    samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return samples.mean(axis=1).astype(np.float32), int(sr)


def write_wav(samples: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.clip(samples, -1.0, 1.0), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# -------------------- audio_analysis
FRAME_S = 0.02


@dataclass
class AudioProfile:
    duration_s: float
    sample_rate: int
    speech_db: float
    floor_db: float
    leading_silence_s: float
    trailing_silence_s: float
    gaps: list[tuple[float, float]]  # internal silences (start, end) longer than the threshold
    longest_gap_s: float
    clipped_fraction: float
    clip_events: list[tuple[float, float]]
    noisy_runs: list[tuple[float, float]]
    end_rel_db: float  # last 60 ms level relative to speech level (0 = as loud as speech)
    voiced_fraction: float

    def to_metrics(self) -> dict:
        return {
            "duration_s": round(self.duration_s, 3),
            "speech_db": round(self.speech_db, 2),
            "floor_db": round(self.floor_db, 2),
            "leading_silence_s": round(self.leading_silence_s, 3),
            "trailing_silence_s": round(self.trailing_silence_s, 3),
            "longest_gap_s": round(self.longest_gap_s, 3),
            "clipped_fraction": round(self.clipped_fraction, 6),
            "end_rel_db": round(self.end_rel_db, 2),
            "voiced_fraction": round(self.voiced_fraction, 3),
        }


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index runs where mask is True."""
    if mask.size == 0:
        return []
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(padded)
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist(), strict=True))


def frame_levels(samples: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame level (dBFS) and spectral flatness (0..1)."""
    frame = max(1, int(FRAME_S * sr))
    n = len(samples) // frame
    if n == 0:
        return np.zeros(0), np.zeros(0)
    x = samples[: n * frame].reshape(n, frame).astype(np.float64)
    rms = np.sqrt(np.mean(x * x, axis=1))
    db = 20 * np.log10(rms + 1e-10)
    power = np.abs(np.fft.rfft(x * np.hanning(frame), axis=1)) ** 2 + 1e-20
    flat = np.exp(np.mean(np.log(power), axis=1)) / np.mean(power, axis=1)
    return db, flat


def analyze(samples: np.ndarray, sr: int, th: dict) -> AudioProfile:
    samples = np.asarray(samples, dtype=np.float32)
    duration = len(samples) / sr if sr else 0.0
    db, flat = frame_levels(samples, sr)
    if db.size == 0:
        return AudioProfile(duration, sr, -200.0, -200.0, duration, duration, [], 0.0, 0.0, [], [], -200.0, 0.0)

    speech_db = float(np.percentile(db, 90))
    floor_db = float(np.percentile(db, 10))
    silent = db < max(speech_db - th["silence_rel_db"], -90.0)
    runs = _runs(silent)
    n = db.size
    leading = trailing = 0.0
    gaps: list[tuple[float, float]] = []
    longest = 0.0
    for s, e in runs:
        length = (e - s) * FRAME_S
        if s == 0:
            leading = length
        elif e == n:
            trailing = length
        else:
            longest = max(longest, length)
            if length >= th["silence_gap_s"]:
                gaps.append((s * FRAME_S, e * FRAME_S))
    if leading >= duration - 1e-9:  # all silent
        trailing = duration

    # clipping: runs of full-scale samples
    clipped = np.abs(samples) >= th["clip_level"]
    clip_events = [(s / sr, e / sr) for s, e in _runs(clipped) if e - s >= th["clip_run_samples"]]
    clipped_fraction = float(clipped.mean())

    # noise-like stretches: flat spectrum AND real energy (not silence)
    noisy = (flat >= th["noise_flatness"]) & (db > max(floor_db, speech_db - 45.0)) & ~silent
    min_frames = int(round(th["noise_run_s"] / FRAME_S))
    noisy_runs = [(s * FRAME_S, e * FRAME_S) for s, e in _runs(noisy) if e - s >= min_frames]

    tail = max(1, int(0.06 / FRAME_S))
    end_rel = float(np.max(db[-tail:]) - speech_db)
    voiced_fraction = float(np.mean(~silent))
    return AudioProfile(
        duration, sr, speech_db, floor_db, leading, trailing, gaps, longest, clipped_fraction, clip_events, noisy_runs, end_rel, voiced_fraction
    )


SNAP_RADIUS_S = 0.15


def frame_rms(samples: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    frame = max(1, int(FRAME_S * sr))
    n = len(samples) // frame
    x = samples[: n * frame].reshape(n, frame).astype(np.float64)
    return np.sqrt(np.mean(x * x, axis=1)), frame


def snap(samples: np.ndarray, sr: int, t: float) -> int:
    """Sample index of the quietest frame start within ±SNAP_RADIUS_S of time t."""
    rms, frame = frame_rms(samples, sr)
    if rms.size == 0:
        return int(t * sr)
    lo = max(0, int((t - SNAP_RADIUS_S) / FRAME_S))
    hi = min(rms.size, int((t + SNAP_RADIUS_S) / FRAME_S) + 1)
    if lo >= hi:
        return min(len(samples), int(t * sr))
    k = lo + int(np.argmin(rms[lo:hi]))
    return k * frame
