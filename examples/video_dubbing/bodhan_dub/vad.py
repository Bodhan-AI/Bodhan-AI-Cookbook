"""Energy-based voice activity detection.

Why hand-rolled rather than a library: the pipeline needs deterministic,
testable boundaries, no extra native dependency, and no model that might be
confused with speech recognition. A frame-energy VAD with an adaptive noise
floor and hysteresis is enough for lecture and interview audio, and it is a
pure function of the samples, so it can be unit-tested exactly.

Thresholds adapt per file:

    threshold = max(noise_floor + 8 dB, peak - 32 dB, -55 dB absolute)

Hysteresis stops a single loud frame from opening a region, and bridging keeps
a short pause inside a sentence from splitting it.

The bridge is 300 ms, and it is deliberately not larger. Raising it to 450 ms
looked like it fixed a clipped word, but on real lecture audio the pauses between
sentences are only 100-300 ms, so a 450 ms bridge collapsed twenty-six seconds of
speech into a single chunk — destroying the per-segment timing the dub depends on.

Truncation is fixed with **tail padding** instead: each region is extended up to
220 ms into the silence that follows it, but never past the next region's start.
That recovers a clipped final consonant without merging anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

ABSOLUTE_FLOOR_DB = -55.0


@dataclass(frozen=True)
class Region:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict[str, float]:
        return {"start": round(self.start_s, 3), "end": round(self.end_s, 3)}


@dataclass(frozen=True)
class VadParams:
    frame_ms: int = 20
    noise_percentile: float = 10.0
    peak_percentile: float = 99.0
    above_noise_db: float = 8.0
    below_peak_db: float = 32.0
    min_dynamic_range_db: float = 6.0
    flat_signal_below_peak_db: float = 12.0
    absolute_floor_db: float = ABSOLUTE_FLOOR_DB
    min_entry_ms: float = 40.0
    bridge_ms: float = 300.0
    min_speech_s: float = 0.20
    pad_s: float = 0.10
    tail_pad_s: float = 0.22


def frame_length(sample_rate: int, frame_ms: int) -> int:
    return max(1, int(round(sample_rate * frame_ms / 1000.0)))


def frame_db(samples: np.ndarray, sample_rate: int, frame_ms: int = 20) -> np.ndarray:
    """Per-frame RMS in dBFS. The trailing partial frame is dropped."""
    length = frame_length(sample_rate, frame_ms)
    count = len(samples) // length
    if count == 0:
        return np.zeros(0, dtype=np.float64)
    trimmed = samples[: count * length].astype(np.float64).reshape(count, length)
    rms = np.sqrt(np.mean(trimmed * trimmed, axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-10))


def threshold_db(db: np.ndarray, params: VadParams) -> float:
    """Adaptive speech threshold for this file.

    Normally the noise floor decides. If the file has almost no dynamic range —
    a continuous recording with no pauses, or a synthetic tone — the tenth
    percentile sits at speech level, so a noise-relative threshold would sit
    above every frame and detect nothing. In that case fall back to a fixed
    offset below the peak, which keeps continuous speech detected.

    Digital silence is still rejected, because the absolute floor wins.
    """
    if db.size == 0:
        return params.absolute_floor_db
    noise = float(np.percentile(db, params.noise_percentile))
    peak = float(np.percentile(db, params.peak_percentile))
    if (peak - noise) < params.min_dynamic_range_db:
        energy_based = peak - params.flat_signal_below_peak_db
    else:
        energy_based = max(noise + params.above_noise_db, peak - params.below_peak_db)
    return max(energy_based, params.absolute_floor_db)


def detect(samples: np.ndarray, sample_rate: int, params: VadParams | None = None) -> list[Region]:
    """Return speech regions, in seconds, sorted and non-overlapping."""
    params = params or VadParams()
    if len(samples) == 0:
        return []

    db = frame_db(samples, sample_rate, params.frame_ms)
    if db.size == 0:
        return []

    duration = len(samples) / float(sample_rate)
    above = db > threshold_db(db, params)

    min_entry = max(1, int(math.ceil(params.min_entry_ms / params.frame_ms)))
    bridge = max(1, int(round(params.bridge_ms / params.frame_ms)))

    raw: list[tuple[int, int]] = []
    index = 0
    total = len(above)
    while index < total:
        if not above[index]:
            index += 1
            continue
        start = index
        while index < total and above[index]:
            index += 1
        if (index - start) < min_entry:
            continue
        end = index
        # Bridge short quiet gaps so a pause inside a sentence does not split it.
        while end < total:
            probe = end
            while probe < total and not above[probe]:
                probe += 1
            if probe >= total or (probe - end) >= bridge:
                break
            end = probe
            while end < total and above[end]:
                end += 1
        raw.append((start, end))
        index = end

    length = frame_length(sample_rate, params.frame_ms)
    regions: list[Region] = []
    for start_frame, end_frame in raw:
        start_s = start_frame * length / sample_rate
        end_s = min(end_frame * length / sample_rate, duration)
        if end_s - start_s >= params.min_speech_s:
            regions.append(Region(start_s, end_s))

    return _pad_without_overlap(regions, duration, params)


def _pad_without_overlap(regions: list[Region], duration: float, params: VadParams) -> list[Region]:
    """Extend each region into nearby silence, never across its neighbour.

    Leading padding is small because a word rarely starts with a soft consonant.
    Trailing padding is larger because final consonants (approximants like "r"
    and "l") are the quietest part of a word and get clipped when a region ends
    on the energy threshold.
    """
    padded: list[Region] = []
    last_end = 0.0
    for index, region in enumerate(regions):
        next_start = regions[index + 1].start_s if index + 1 < len(regions) else duration
        start = max(last_end, region.start_s - params.pad_s)
        end = min(next_start, region.end_s + params.tail_pad_s)
        end = max(end, start)
        padded.append(Region(start, end))
        last_end = end
    return padded
