"""Turn speech regions into transcription chunks.

The ASR model is trained on clips of up to 30 seconds and degrades into
repetition beyond that, so a chunk is capped at 28 s with headroom. The cap is
enforced by cutting at the quietest internal frame — never mid-word — and by
preferring the latest legal cut, because every cut costs a little accuracy at
its edges.

Each chunk is addressed by sample index as well as by time, so cutting the
buffer later is exact and cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import vad
from .vad import Region, VadParams

MAX_CHUNK_S = 28.0
MIN_CHUNK_S = 0.80


@dataclass(frozen=True)
class Chunk:
    index: int
    start_s: float
    end_s: float
    start_sample: int
    end_sample: int
    regions: int
    split: bool

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "start": round(self.start_s, 3),
            "end": round(self.end_s, 3),
            "duration_s": round(self.duration_s, 3),
            "regions": self.regions,
            "split": self.split,
        }


def plan(
    samples: np.ndarray,
    sample_rate: int,
    *,
    max_chunk_s: float = MAX_CHUNK_S,
    min_chunk_s: float = MIN_CHUNK_S,
    vad_params: VadParams | None = None,
) -> list[Chunk]:
    """Return chunks covering all detected speech, in order."""
    params = vad_params or VadParams()
    regions = vad.detect(samples, sample_rate, params)
    if not regions:
        return []

    length = vad.frame_length(sample_rate, params.frame_ms)
    db = vad.frame_db(samples, sample_rate, params.frame_ms)

    pieces: list[tuple[float, float, int, bool]] = []
    for region in regions:
        for start_s, end_s, split in _split_region(region, db, sample_rate, length, max_chunk_s):
            pieces.append((start_s, end_s, 1, split))

    # Fold a tiny tail back into the piece before it rather than transcribing a blip.
    if len(pieces) > 1:
        last_start, last_end, _, last_split = pieces[-1]
        if (last_end - last_start) < min_chunk_s and last_split:
            prev_start, _, prev_regions, prev_split = pieces[-2]
            pieces[-2] = (prev_start, last_end, prev_regions, prev_split)
            pieces.pop()

    chunks: list[Chunk] = []
    duration = len(samples) / float(sample_rate)
    for start_s, end_s, region_count, split in pieces:
        start_s = max(0.0, min(start_s, duration))
        end_s = max(start_s, min(end_s, duration))
        if end_s - start_s <= 0:
            continue
        chunks.append(
            Chunk(
                index=len(chunks),
                start_s=start_s,
                end_s=end_s,
                start_sample=int(round(start_s * sample_rate)),
                end_sample=int(round(end_s * sample_rate)),
                regions=region_count,
                split=split,
            )
        )
    return chunks


def _split_region(
    region: Region,
    db: np.ndarray,
    sample_rate: int,
    frame_samples: int,
    max_chunk_s: float,
) -> list[tuple[float, float, bool]]:
    if region.duration_s <= max_chunk_s:
        return [(region.start_s, region.end_s, False)]

    pieces: list[tuple[float, float, bool]] = []
    cursor = region.start_s
    guard = 0
    while region.end_s - cursor > max_chunk_s:
        guard += 1
        if guard > 10_000:  # pathological input; refuse to spin
            break
        low = cursor + max_chunk_s * 0.5
        high = cursor + max_chunk_s
        cut = _quietest_cut(db, sample_rate, frame_samples, low, high)
        cut = min(max(cut, cursor + 0.5), region.end_s)
        pieces.append((cursor, cut, True))
        cursor = cut
    pieces.append((cursor, region.end_s, bool(pieces)))
    return pieces


def _quietest_cut(
    db: np.ndarray,
    sample_rate: int,
    frame_samples: int,
    low_s: float,
    high_s: float,
) -> float:
    """Middle of the quietest frame in [low_s, high_s], or high_s if empty."""
    if db.size == 0:
        return high_s
    first = max(0, int(low_s * sample_rate) // frame_samples)
    last = min(db.size, int(high_s * sample_rate) // frame_samples)
    if last <= first:
        return high_s
    window = db[first:last]
    offset = int(np.argmin(window))
    centre = (first + offset + 0.5) * frame_samples / sample_rate
    return min(max(centre, low_s), high_s)
