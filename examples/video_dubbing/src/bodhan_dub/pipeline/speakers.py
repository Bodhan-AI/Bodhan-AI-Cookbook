"""Lightweight speaker handling: pitch-based male/female labelling.

Indic-Transcribe has no diarization and most Indic-Speak languages ship one male
and one female voice, so a robust, dependency-free F0 heuristic is enough to keep
each speaker's voice consistent for the common two-speaker case. We estimate the
median voiced pitch per segment via autocorrelation and label M/F against an
adaptive threshold derived from the video's own pitch distribution.
"""

from __future__ import annotations

import numpy as np

from ..ffmpeg import read_wav
from ..logging_setup import get_logger
from ..types import Segment

log = get_logger("speakers")

F0_MIN, F0_MAX = 80.0, 400.0        # human speech range (Hz)
DEFAULT_SPLIT = 165.0               # typical M/F boundary, used as a fallback


def estimate_f0(samples: np.ndarray, sr: int) -> float | None:
    """Median fundamental frequency over voiced frames, or None if unvoiced."""
    flen = int(0.04 * sr)
    if len(samples) < flen:
        return None
    pitches: list[float] = []
    min_lag, max_lag = int(sr / F0_MAX), int(sr / F0_MIN)
    for i in range(0, len(samples) - flen, flen):
        frame = samples[i : i + flen].astype(np.float64)
        if np.sqrt(np.mean(frame**2)) < 1e-3:
            continue
        frame = frame - frame.mean()
        corr = np.correlate(frame, frame, mode="full")[len(frame) - 1 :]
        if corr[0] <= 0:
            continue
        seg = corr[min_lag:max_lag]
        if len(seg) == 0:
            continue
        lag = min_lag + int(np.argmax(seg))
        if corr[lag] / corr[0] > 0.3:  # require a clear periodic peak
            pitches.append(sr / lag)
    return float(np.median(pitches)) if pitches else None


def assign_speakers(segments: list[Segment], asr_wav: str) -> list[Segment]:
    samples, sr = read_wav(asr_wav)
    f0s: dict[int, float] = {}
    for seg in segments:
        if not seg.is_speech:
            continue
        a, b = int(seg.start * sr), int(seg.end * sr)
        f0 = estimate_f0(samples[a:b], sr)
        if f0:
            f0s[seg.index] = f0

    split = DEFAULT_SPLIT
    if len(f0s) >= 4:
        vals = sorted(f0s.values())
        # If the distribution is clearly bimodal, split at the widest internal gap.
        gaps = [(vals[i + 1] - vals[i], (vals[i + 1] + vals[i]) / 2) for i in range(len(vals) - 1)]
        gap, mid = max(gaps)
        if gap > 40 and F0_MIN < mid < F0_MAX:
            split = mid

    for seg in segments:
        if not seg.is_speech:
            continue
        f0 = f0s.get(seg.index)
        if f0 is None:
            seg.speaker = seg.speaker or "F"  # default voice when pitch is unclear
        else:
            seg.speaker = "M" if f0 < split else "F"
    log.info("assigned speakers", extra={"event": "speakers",
             "extra": {"split_hz": round(split, 1), "voiced_segments": len(f0s)}})
    return segments


def gender_for(speaker: str | None) -> str:
    return "male" if (speaker or "").startswith("M") else "female"
