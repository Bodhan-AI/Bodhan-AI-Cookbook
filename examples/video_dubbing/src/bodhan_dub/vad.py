"""Voice activity detection: find where speech is in a 16 kHz mono WAV."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

Region = tuple[float, float]  # (start_sec, end_sec)


def _read_16k(wav_path: Path) -> np.ndarray:
    wav, sr = sf.read(str(wav_path), dtype="float32")
    if sr != 16_000:
        raise ValueError(f"Expected 16 kHz audio, got {sr} Hz: {wav_path}")
    return wav if wav.ndim == 1 else wav.mean(axis=1)


def silero_regions(wav_path: Path, threshold: float = 0.5) -> list[Region]:
    """Speech regions using Silero VAD (handles music/noise better than energy)."""
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    model = load_silero_vad()
    ts = get_speech_timestamps(
        torch.from_numpy(_read_16k(wav_path)), model, sampling_rate=16_000,
        threshold=threshold, min_speech_duration_ms=100, min_silence_duration_ms=100,
        speech_pad_ms=0, return_seconds=True,
    )
    return [(float(t["start"]), float(t["end"])) for t in ts]


def energy_regions(wav_path: Path, frame_ms: int = 30) -> list[Region]:
    """Fallback: simple energy VAD. Treats loud music as speech, so less accurate."""
    wav = _read_16k(wav_path)
    n = int(16_000 * frame_ms / 1000)
    frames = wav[: len(wav) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-8)
    lo, hi = np.percentile(db, 10), np.percentile(db, 95)
    voiced = db > lo + 0.35 * (hi - lo)
    regions, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            regions.append((start * frame_ms / 1000, i * frame_ms / 1000))
            start = None
    if start is not None:
        regions.append((start * frame_ms / 1000, len(voiced) * frame_ms / 1000))
    return regions


EDGE_DEPTH_DB = 30.0  # a speech edge is where energy is within this many dB of the region's speech level


def refine_edges(wav: np.ndarray, sr: int, regions: list[Region], search: float = 0.15,
                 frame: float = 0.01, min_rise_db: float = 6.0, depth_db: float = EDGE_DEPTH_DB,
                 sustain: int = 3) -> list[Region]:
    """Move each region's start/end (by at most `search` s) to where the speech actually starts/ends.

    One rule for every signal (source voice, dubbed clip, final mix): the edge is where the energy rises to
    within `depth_db` of the region's speech level (90th percentile), but never closer than `min_rise_db`
    to the local noise floor. Tying the threshold to the speech level (not the floor) gives the same edge
    for the same voice in a noisy room or in digital silence, so source and dub are measured alike.
    If the region is not clearly above the floor, or no sustained crossing is found, the VAD edge is kept.
    """
    hop = max(1, int(sr * frame))
    n = len(wav) // hop
    if n == 0 or not regions:
        return list(regions)
    fr = wav[: n * hop].reshape(n, hop).astype("float64")
    db = 10 * np.log10((fr ** 2).mean(axis=1) + 1e-12)
    total = n * frame

    def fidx(a: float, b: float) -> tuple[int, int]:
        return max(0, int(a / frame)), min(n, int(np.ceil(b / frame)))

    regs = sorted(regions)
    out: list[Region] = []
    for i, (s, e) in enumerate(regs):
        lo_lim = out[-1][1] if out else 0.0
        hi_lim = regs[i + 1][0] if i + 1 < len(regs) else total
        a, b = fidx(max(lo_lim, s - 0.5), s)
        c, d = fidx(e, min(hi_lim, e + 0.5))
        quiet = np.concatenate([db[a:b], db[c:d]])
        floor = float(np.percentile(quiet, 20)) if len(quiet) >= 5 else float(np.percentile(db, 10))
        x, y = fidx(s, e)
        peak = float(np.percentile(db[x:y], 90)) if y > x else floor
        if peak - floor < min_rise_db:
            out.append((s, e))
            continue
        thr = max(floor + min_rise_db, peak - depth_db)
        ns, ne = s, e
        f0, f1 = fidx(max(lo_lim, s - search), min(e, s + search))
        for f in range(f0, f1):
            if f + sustain <= n and np.all(db[f : f + sustain] > thr):
                ns = f * frame
                break
        g0, g1 = fidx(max(ns, e - search), min(hi_lim, e + search))
        for f in range(g1 - 1, g0 - 1, -1):
            if f - sustain + 1 >= 0 and np.all(db[f - sustain + 1 : f + 1] > thr):
                ne = (f + 1) * frame
                break
        if ne - ns < 0.05:
            ns, ne = s, e
        out.append((round(ns, 3), round(ne, 3)))
    return out


def detect_speech(wav_path: str | Path, backend: str = "auto") -> list[Region]:
    """backend: 'silero', 'energy' or 'auto' (silero, falling back to energy if unavailable)."""
    wav_path = Path(wav_path)
    if backend in ("auto", "silero"):
        try:
            regions = silero_regions(wav_path)
            log.info("vad: silero found %d speech regions", len(regions))
            return regions
        except ImportError:
            if backend == "silero":
                raise
            log.warning("vad: silero-vad/torch not installed, falling back to energy VAD")
    regions = energy_regions(wav_path)
    log.info("vad: energy found %d speech regions", len(regions))
    return regions
