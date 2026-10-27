"""Split a call into pieces of at most ``max_len`` seconds, cutting at pauses.

Bodhan Speech-to-Text takes at most 30 s per request and returns no timestamps,
so each piece's start/end becomes the transcript timestamp. Cutting at pauses
keeps words whole, and dropping long silences saves cost (billing is per second).

Two entry points:

* ``split_by_pauses`` – no speaker information; pieces follow the speech.
* ``split_by_turns``  – speaker turns from a diarization model; every piece
  belongs to exactly one speaker turn and is cut at pauses inside that turn.

Everything here is pure numpy so it can be unit-tested with synthetic audio.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from .audio import SAMPLE_RATE

FRAME_SECONDS = 0.02
MIN_SPEECH_DB = 40.0  # about -50 dBFS; quieter frames are never speech


@dataclass(frozen=True)
class Piece:
    start: float
    end: float
    speaker: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str  # raw label from the diarizer, e.g. "speaker_0"


@dataclass(frozen=True)
class SegmenterConfig:
    max_len: float = 28.0  # hard limit; Bodhan allows 30 s
    target_len: float = 15.0  # past this, prefer to start a new piece at the next pause
    min_pause: float = 0.35  # shorter quiet gaps are treated as part of the speech
    long_pause: float = 1.0  # always start a new piece after a pause this long
    min_speech: float = 0.25  # drop blips shorter than this (clicks, breaths)
    pad: float = 0.15  # keep a little audio around each speech region


def frame_energy_db(samples: np.ndarray, sr: int = SAMPLE_RATE, frame_s: float = FRAME_SECONDS) -> np.ndarray:
    """RMS level of each frame in dB relative to one int16 step."""
    n = int(sr * frame_s)
    count = len(samples) // n
    if count == 0:
        return np.zeros(0)
    out = np.empty(count)
    step = 3000  # frames per batch (60 s) keeps memory flat on long calls
    for i in range(0, count, step):
        block = samples[i * n : min(count, i + step) * n].astype(np.float64).reshape(-1, n)
        out[i : i + len(block)] = np.sqrt(np.mean(block * block, axis=1))
    return 20 * np.log10(out + 1.0)


def speech_mask(db: np.ndarray) -> np.ndarray:
    """True where a frame is louder than an adaptive threshold above the noise floor."""
    if db.size == 0:
        return np.zeros(0, dtype=bool)
    audible = db > MIN_SPEECH_DB
    floor = float(np.percentile(db, 10))
    spread = float(np.percentile(db, 99)) - floor
    if spread < 6:  # no quiet parts at all: either constant speech or constant line noise
        return audible
    return audible & (db > floor + min(12.0, max(6.0, 0.3 * spread)))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [start, end) where ``mask`` is True."""
    if mask.size == 0:
        return []
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def speech_regions(
    db: np.ndarray, cfg: SegmenterConfig, lo: float = 0.0, hi: float | None = None
) -> list[tuple[float, float]]:
    """Speech regions (seconds) inside [lo, hi], with short gaps bridged and blips dropped."""
    total = db.size * FRAME_SECONDS
    hi = total if hi is None else min(hi, total)
    a, b = int(lo / FRAME_SECONDS), int(np.ceil(hi / FRAME_SECONDS))
    regions: list[list[float]] = []
    for s, e in _runs(speech_mask(db)[a:b] if a < b else np.zeros(0, dtype=bool)):
        start, end = (a + s) * FRAME_SECONDS, (a + e) * FRAME_SECONDS
        if regions and start - regions[-1][1] < cfg.min_pause:
            regions[-1][1] = end
        else:
            regions.append([start, end])
    kept = [(s, e) for s, e in regions if e - s >= cfg.min_speech]
    padded: list[tuple[float, float]] = []
    for i, (s, e) in enumerate(kept):
        s = max(lo, s - cfg.pad, padded[-1][1] if padded else lo)
        nxt = kept[i + 1][0] if i + 1 < len(kept) else hi
        e = min(hi, e + cfg.pad, (e + nxt) / 2 if i + 1 < len(kept) else hi)
        padded.append((round(s, 2), round(e, 2)))
    return padded


def _split_long(start: float, end: float, db: np.ndarray, cfg: SegmenterConfig) -> list[tuple[float, float]]:
    """Cut a region longer than ``max_len`` at its quietest moments."""
    out: list[tuple[float, float]] = []
    smooth = np.convolve(db, np.ones(10) / 10, mode="same") if db.size >= 10 else db
    while end - start > cfg.max_len:
        a = int((start + cfg.max_len * 0.4) / FRAME_SECONDS)
        b = int((start + cfg.max_len) / FRAME_SECONDS)
        window = smooth[a:b]
        cut = (a + int(np.argmin(window))) * FRAME_SECONDS if window.size else start + cfg.max_len
        cut = round(min(cut, start + cfg.max_len), 2)
        out.append((start, cut))
        start = cut
    out.append((start, end))
    return out


def _pack(regions: Iterable[tuple[float, float]], db: np.ndarray, cfg: SegmenterConfig) -> list[tuple[float, float]]:
    """Join neighbouring regions into pieces close to ``target_len``, never above ``max_len``."""
    pieces: list[list[float]] = []
    for rs, re_ in regions:
        for s, e in _split_long(rs, re_, db, cfg):
            if pieces:
                cur = pieces[-1]
                gap = s - cur[1]
                fits = e - cur[0] <= cfg.max_len
                wants = gap < cfg.long_pause and (cur[1] - cur[0] < cfg.target_len or gap < cfg.min_pause)
                if fits and wants:
                    cur[1] = e
                    continue
            pieces.append([s, e])
    return [(s, e) for s, e in pieces]


def split_by_pauses(samples: np.ndarray, cfg: SegmenterConfig | None = None) -> list[Piece]:
    cfg = cfg or SegmenterConfig()
    db = frame_energy_db(samples)
    return [Piece(s, e) for s, e in _pack(speech_regions(db, cfg), db, cfg)]


def normalize_turns(turns: Iterable[SpeakerTurn], merge_gap: float = 0.5) -> list[SpeakerTurn]:
    """Make turns non-overlapping and relabel speakers 'Speaker 1', 'Speaker 2', ... by first appearance.

    Diarization output can overlap (two people talking at once). Each moment of
    audio must go to one piece, so a later turn wins from where it starts; a
    turn completely inside another is dropped.
    """
    flat: list[list] = []  # [start, end, raw speaker]
    for t in sorted((t for t in turns if t.end > t.start), key=lambda t: (t.start, t.end)):
        prev = flat[-1] if flat else None
        if prev and t.end <= prev[1]:
            continue  # contained in the previous turn
        if prev and prev[2] == t.speaker and t.start - prev[1] <= merge_gap:
            prev[1] = t.end  # same speaker continues (overlapping or after a short gap)
            continue
        if prev and t.start < prev[1]:
            prev[1] = t.start  # different speaker cuts in: the later turn wins
        flat.append([t.start, t.end, t.speaker])

    labels: dict[str, str] = {}
    out: list[SpeakerTurn] = []
    for s, e, spk in flat:
        if e - s > 0.05:
            label = labels.setdefault(spk, f"Speaker {len(labels) + 1}")
            out.append(SpeakerTurn(round(s, 2), round(e, 2), label))
    return out


def split_by_turns(
    samples: np.ndarray, turns: Iterable[SpeakerTurn], cfg: SegmenterConfig | None = None
) -> list[Piece]:
    cfg = cfg or SegmenterConfig()
    db = frame_energy_db(samples)
    pieces: list[Piece] = []
    for turn in normalize_turns(turns):
        regions = speech_regions(db, cfg, turn.start, turn.end)
        if not regions and turn.end - turn.start >= cfg.min_speech:
            regions = [(turn.start, turn.end)]  # the diarizer heard speech the energy check missed
        pieces.extend(Piece(s, e, turn.speaker) for s, e in _pack(regions, db, cfg))
    return pieces
