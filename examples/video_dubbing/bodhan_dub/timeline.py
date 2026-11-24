"""Placing clips on a timeline.

The dub is built as a single track the exact length of the input video: silent
where there is no speech, clips at their source start times, mixed additively if
one overruns into the next. Additive rather than overwrite is deliberate — an
overrun should be audible to a reviewer, not silently clipped.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from . import media


def build_timeline(
    clips: list[tuple[float, np.ndarray]],
    total_s: float,
    sample_rate: int,
) -> np.ndarray:
    total_samples = max(0, int(round(total_s * sample_rate)))
    track = np.zeros(total_samples, dtype=np.float32)
    for start_s, samples in clips:
        if samples.size == 0:
            continue
        start = max(0, int(round(start_s * sample_rate)))
        if start >= total_samples:
            continue
        end = min(total_samples, start + samples.size)
        track[start:end] += samples[: end - start]
    np.clip(track, -1.0, 1.0, out=track)
    return track


def write_timeline(
    path: Any,
    clips: list[tuple[float, np.ndarray]],
    total_s: float,
    sample_rate: int,
) -> np.ndarray:
    track = build_timeline(clips, total_s, sample_rate)
    media.write_wav(path, track, sample_rate)
    return track
