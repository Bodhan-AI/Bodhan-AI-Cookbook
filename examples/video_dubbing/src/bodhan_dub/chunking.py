"""Group VAD speech regions into chunks the ASR can take (<= ~28 s), cutting at pauses.

The ASR API returns no timestamps, so chunk start/end (from VAD) are the only timing we have.
Pure logic lives in `build_chunks`; audio I/O is in `chunk_audio`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .vad import Region, detect_speech, refine_edges

log = logging.getLogger(__name__)


@dataclass
class ChunkParams:
    max_len: float = 28.0      # hard cap per chunk (API silently truncates > 30 s)
    cut_gap: float = 0.6       # pauses >= this always start a new chunk
    merge_gap: float = 0.2     # gaps < this are treated as one continuous region
    pad: float = 0.15          # padding added at chunk edges
    min_speech: float = 0.3    # drop chunks with less speech than this


@dataclass
class Chunk:
    id: int
    start: float
    end: float
    speech: float              # seconds of actual speech inside the chunk
    path: str | None = None
    vad_start: float | None = None   # speech start (no padding, energy-refined): used for dub placement
    vad_end: float | None = None     # speech end (no padding, energy-refined)
    regions: list | None = None      # real speech sub-regions [[start, end], ...] inside the chunk (pauses between)

    @property
    def duration(self) -> float:
        return self.end - self.start


EnergyFn = Callable[[float, float], float]  # (t0, t1) -> time of lowest energy in [t0, t1]


def merge_regions(regions: list[Region], merge_gap: float) -> list[Region]:
    """Sort and join regions separated by gaps shorter than merge_gap."""
    out: list[Region] = []
    for s, e in sorted(regions):
        if e <= s:
            continue
        if out and s - out[-1][1] < merge_gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _piece(subs: list[Region]) -> tuple[float, float, float]:
    return subs[0][0], subs[-1][1], sum(e - s for s, e in subs)


def split_group(subs: list[Region], budget: float, energy_fn: EnergyFn | None = None) -> list[tuple[float, float, float]]:
    """Split one continuous group (no pauses >= cut_gap) into pieces of length <= budget.

    Cuts at the longest internal gap; hard-cuts inside a region only when it alone exceeds budget.
    Returns (start, end, speech_seconds) tuples.
    """
    subs = list(subs)
    out: list[tuple[float, float, float]] = []
    i = 0
    while i < len(subs):
        start = subs[i][0]
        if subs[-1][1] - start <= budget:
            out.append(_piece(subs[i:]))
            break
        cands = [j for j in range(i, len(subs) - 1) if subs[j][1] - start <= budget]
        if not cands:  # one region longer than budget: hard cut near budget at lowest energy
            s, e = subs[i]
            lo, hi = s + 0.7 * budget, s + budget
            cut = energy_fn(lo, hi) if energy_fn else hi
            cut = min(max(cut, s + 0.1), hi)
            out.append((s, cut, cut - s))
            subs[i] = (cut, e)
            continue
        preferred = [j for j in cands if subs[j][1] - start >= 0.4 * budget] or cands
        j = max(preferred, key=lambda k: subs[k + 1][0] - subs[k][1])
        out.append(_piece(subs[i : j + 1]))
        i = j + 1
    return out


def build_chunks(
    regions: list[Region],
    total_dur: float,
    params: ChunkParams = ChunkParams(),
    energy_fn: EnergyFn | None = None,
) -> list[Chunk]:
    """Turn VAD speech regions into chunks. Pure function (no audio I/O)."""
    p = params
    subs = merge_regions(regions, p.merge_gap)
    if not subs:
        return []

    # 1. groups separated by pauses >= cut_gap
    groups: list[list[Region]] = [[subs[0]]]
    for r in subs[1:]:
        if r[0] - groups[-1][-1][1] >= p.cut_gap:
            groups.append([r])
        else:
            groups[-1].append(r)

    # 2. split any group that is too long (budget leaves room for padding)
    budget = p.max_len - 2 * p.pad
    pieces: list[tuple[float, float, float]] = []
    for g in groups:
        pieces.extend(split_group(g, budget, energy_fn))

    # 3. drop near-empty pieces
    kept = []
    for s, e, sp in pieces:
        if sp < p.min_speech:
            log.info("chunking: dropped %.2f-%.2fs (only %.2fs speech)", s, e, sp)
        else:
            kept.append((s, e, sp))

    # 4. pad edges, never crossing the midpoint to the neighbour
    chunks: list[Chunk] = []
    for k, (s, e, sp) in enumerate(kept):
        lo = (kept[k - 1][1] + s) / 2 if k > 0 else 0.0
        hi = (e + kept[k + 1][0]) / 2 if k + 1 < len(kept) else total_dur
        chunks.append(Chunk(id=k + 1, start=round(max(lo, s - p.pad), 3),
                            end=round(min(hi, e + p.pad), 3), speech=round(sp, 3),
                            vad_start=round(s, 3), vad_end=round(e, 3)))
    return chunks


def assign_regions(chunks: list[Chunk], regions: list[Region], min_len: float = 0.02) -> list[Chunk]:
    """Attach the (refined) speech sub-regions that fall inside each chunk, and set vad_start/vad_end from them.

    Regions are clipped to the chunk's padded window, so a region split by a hard cut is split too.
    """
    for c in chunks:
        subs = [[round(max(a, c.start), 3), round(min(b, c.end), 3)] for a, b in regions if b > c.start and a < c.end]
        subs = [r for r in subs if r[1] - r[0] >= min_len]
        if not subs:
            subs = [[c.vad_start if c.vad_start is not None else c.start, c.vad_end if c.vad_end is not None else c.end]]
        c.regions = subs
        c.vad_start, c.vad_end = subs[0][0], subs[-1][1]
    return chunks


def _min_energy_fn(wav: np.ndarray, sr: int) -> EnergyFn:
    def fn(t0: float, t1: float) -> float:
        a, b = int(t0 * sr), min(int(t1 * sr), len(wav))
        win = int(0.02 * sr)
        if b - a < 2 * win:
            return t1
        seg = wav[a:b][: (b - a) // win * win].reshape(-1, win)
        return t0 + (int(np.argmin((seg**2).mean(axis=1))) + 0.5) * win / sr
    return fn


def chunk_audio(
    wav_path: str | Path,
    out_dir: str | Path,
    params: ChunkParams = ChunkParams(),
    backend: str = "auto",
    asr_wav: str | Path | None = None,
) -> list[Chunk]:
    """VAD -> chunks -> write chunk_XXXX.wav files and chunks.json into out_dir.

    wav_path is what speech timing is measured on (the separated voice when available). The chunk files
    sent to ASR are cut from asr_wav if given (same timeline and rate), else from wav_path.
    """
    wav_path, out_dir = Path(wav_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav, sr = sf.read(str(wav_path), dtype="float32")
    wav = wav if wav.ndim == 1 else wav.mean(axis=1)
    total = len(wav) / sr
    src = wav
    if asr_wav is not None:
        src, sr2 = sf.read(str(asr_wav), dtype="float32")
        src = src if src.ndim == 1 else src.mean(axis=1)
        if sr2 != sr:
            raise ValueError(f"asr_wav is {sr2} Hz, timing audio is {sr} Hz")

    regions = detect_speech(wav_path, backend)
    # Cut positions come from the raw VAD (stable, so cached ASR stays valid); timing uses refined edges.
    chunks = build_chunks(regions, total, params, _min_energy_fn(wav, sr))
    assign_regions(chunks, refine_edges(wav, sr, merge_regions(regions, params.merge_gap)))
    for c in chunks:
        p = out_dir / f"chunk_{c.id:04d}.wav"
        sf.write(str(p), src[int(c.start * sr) : int(c.end * sr)], sr, subtype="PCM_16")
        c.path = str(p)
    (out_dir / "chunks.json").write_text(json.dumps([asdict(c) for c in chunks], indent=2), encoding="utf-8")
    log.info("chunking: %d chunks from %.1fs audio (longest %.1fs)", len(chunks), total,
             max((c.duration for c in chunks), default=0))
    return chunks


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cs = chunk_audio(sys.argv[1], sys.argv[2])
    for c in cs:
        print(f"{c.id:3d}  {c.start:8.2f} - {c.end:8.2f}  ({c.duration:5.1f}s, speech {c.speech:5.1f}s)")
