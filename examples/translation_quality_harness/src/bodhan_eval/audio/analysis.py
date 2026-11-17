"""Frame-level signal statistics: silence, clipping and noise-like stretches."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .io import Audio

FRAME_S = 0.025
HOP_S = 0.010


@dataclass
class AudioStats:
    duration: float
    rate: int
    peak: float
    rms_db: float
    speech_start: float
    speech_end: float
    silence_threshold_db: float
    internal_silences: list[tuple[float, float]] = field(default_factory=list)
    clipped_fraction: float = 0.0
    clipped_segments: list[tuple[float, float]] = field(default_factory=list)
    noise_segments: list[tuple[float, float]] = field(default_factory=list)
    silent: bool = False

    @property
    def speech_duration(self) -> float:
        return max(self.speech_end - self.speech_start, 0.0)

    @property
    def longest_internal_silence(self) -> float:
        return max((e - s for s, e in self.internal_silences), default=0.0)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["speech_duration"] = round(self.speech_duration, 3)
        data["longest_internal_silence"] = round(self.longest_internal_silence, 3)
        return data


def _frames(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if len(x) < frame:
        x = np.pad(x, (0, frame - len(x)))
    count = 1 + (len(x) - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    return x[idx]


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) index pairs of consecutive True values, end exclusive."""
    if not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    diff = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def analyze(audio: Audio, *, min_pause_s: float = 0.25, noise_flatness: float = 0.45, min_noise_s: float = 0.25) -> AudioStats:
    x = audio.samples.astype(np.float64)
    rate = audio.rate or 1
    duration = len(x) / rate
    if len(x) == 0:
        return AudioStats(0.0, rate, 0.0, -120.0, 0.0, 0.0, -120.0, silent=True)
    frame, hop = max(int(FRAME_S * rate), 16), max(int(HOP_S * rate), 8)
    frames = _frames(x, frame, hop)
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    peak = float(np.max(np.abs(x)))
    speech_level = float(np.percentile(db, 90))
    threshold = max(-55.0, speech_level - 30.0)
    voiced = db > threshold
    if not voiced.any() or speech_level < -60:
        return AudioStats(
            round(duration, 3), rate, round(peak, 4), round(float(20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)), 2), 0.0, 0.0, round(threshold, 2), silent=True
        )

    first, last = int(np.argmax(voiced)), int(len(voiced) - 1 - np.argmax(voiced[::-1]))
    speech_start, speech_end = first * hop / rate, min((last * hop + frame) / rate, duration)
    silences = []
    for s, e in _runs(~voiced[first : last + 1]):
        start_s, end_s = (first + s) * hop / rate, (first + e) * hop / rate + (frame - hop) / rate
        if end_s - start_s >= min_pause_s:
            silences.append((round(start_s, 3), round(end_s, 3)))

    clipped_mask = np.abs(x) >= 0.985 * max(peak, 1e-9)
    clipped_mask &= peak >= 0.95
    clip_runs = [(s, e) for s, e in _runs(clipped_mask) if e - s >= 3]
    clipped_samples = sum(e - s for s, e in clip_runs)
    clip_segments = _merge([(s / rate, e / rate) for s, e in clip_runs], gap=0.05)

    window = np.hanning(frame)
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2 + 1e-12
    flatness = np.exp(np.mean(np.log(spectrum), axis=1)) / np.mean(spectrum, axis=1)
    noisy = (flatness > noise_flatness) & voiced
    noise_segments = []
    for s, e in _runs(noisy):
        start_s, end_s = s * hop / rate, (e * hop + frame - hop) / rate
        if end_s - start_s >= min_noise_s:
            noise_segments.append((round(start_s, 3), round(end_s, 3)))

    return AudioStats(
        duration=round(duration, 3),
        rate=rate,
        peak=round(peak, 4),
        rms_db=round(float(20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)), 2),
        speech_start=round(speech_start, 3),
        speech_end=round(speech_end, 3),
        silence_threshold_db=round(threshold, 2),
        internal_silences=silences,
        clipped_fraction=round(clipped_samples / len(x), 6),
        clipped_segments=[(round(a, 3), round(b, 3)) for a, b in clip_segments],
        noise_segments=noise_segments,
    )


def _merge(segments: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, e in sorted(segments):
        if out and s - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out
