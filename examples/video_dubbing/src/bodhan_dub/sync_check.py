"""Independent sync measurement on the FINAL audio, not on our own placement bookkeeping.

Per unit      : where the dubbed voice really starts/ends (VAD on the output) vs the source speech window.
Unit-free     : (a) speech-activity overlap (IoU) between source speech and dubbed speech;
                (b) phrase onsets: for every place the speaker starts again after a pause, is there a dubbed
                    onset within +-0.3 s?  These two do not depend on how units were cut, so different runs
                    can be compared fairly.
Global offset : cross-correlation of the planned speech track with the output, to catch mux/timebase shifts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import extract_audio
from .chunking import merge_regions
from .vad import detect_speech, refine_edges

log = logging.getLogger(__name__)

START_TOL = 0.3
END_TOL = 0.5
PHRASE_GAP = 0.5   # a source onset after at least this much silence counts as a phrase onset
GRID = 0.01


def _mask(regions: list[tuple[float, float]], t0: float, t1: float) -> np.ndarray:
    n = max(1, int(np.ceil((t1 - t0) / GRID)))
    m = np.zeros(n, dtype=bool)
    for a, b in regions:
        i, j = int(max(0.0, a - t0) / GRID), int(min(t1 - t0, b - t0) / GRID)
        if j > i:
            m[i:j] = True
    return m


def _envelope(path: str | Path, frame: float = 0.005) -> np.ndarray:
    w, sr = sf.read(str(path), dtype="float32")
    w = w if w.ndim == 1 else w.mean(axis=1)
    hop = max(1, int(sr * frame))
    n = len(w) // hop
    e = np.sqrt((w[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    return (e - e.mean()) / (e.std() + 1e-9)


def global_offset(output_wav: str | Path, planned_wav: str | Path, max_lag: float = 1.0, frame: float = 0.005) -> float:
    """Seconds by which the output is later than the plan (0 = perfectly aligned)."""
    a, b = _envelope(output_wav, frame), _envelope(planned_wav, frame)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    L = int(max_lag / frame)
    best, best_lag = -np.inf, 0
    for lag in range(-L, L + 1):
        x = np.dot(a[max(0, lag) : n + min(0, lag)], b[max(0, -lag) : n - max(0, lag)])
        if x > best:
            best, best_lag = x, lag
    return round(best_lag * frame, 3)


def as_16k_mono(media: str | Path, work_dir: str | Path, name: str = "sync_input_16k.wav") -> Path:
    """The file itself if it is already a 16 kHz mono WAV, else a container-aligned 16 kHz mono copy."""
    media = Path(media)
    if media.suffix.lower() == ".wav":
        info = sf.info(str(media))
        if info.samplerate == 16_000 and info.channels == 1:
            return media
    return extract_audio(media, Path(work_dir) / name)


def speech_regions(wav16: str | Path, backend: str = "auto") -> list[tuple[float, float]]:
    """Speech regions of a 16 kHz mono file with the same detector + edge rule used on the source."""
    w, sr = sf.read(str(wav16), dtype="float32")
    w = w if w.ndim == 1 else w.mean(axis=1)
    return refine_edges(w, sr, merge_regions(detect_speech(wav16, backend), 0.05))


def unit_edges(out_regs: list[tuple[float, float]], recs: list[dict]) -> dict:
    """Where each placed unit's voice really starts/ends in a measured signal: {id: {"on", "off"}}.

    Each unit owns a territory (halfway from the previous unit's planned sound to halfway before the next
    one's); the first and last detected sound inside it are its edges. Units with no sound are left out.
    """
    placed = sorted([r for r in recs if "placed_start" in r], key=lambda r: r.get("onset", r["placed_start"]))
    on = [r.get("onset", r["placed_start"]) for r in placed]
    off = [r.get("voiced_end_at", r["placed_end"]) for r in placed]
    out = {}
    for k, r in enumerate(placed):
        lo = (off[k - 1] + on[k]) / 2 if k > 0 else on[k] - 1.0
        hi = (off[k] + on[k + 1]) / 2 if k + 1 < len(placed) else off[k] + 1.0
        inside = [(max(a, lo), min(b, hi)) for a, b in out_regs if b > lo and a < hi]
        inside = [(a, b) for a, b in inside if b - a >= 0.03]
        if inside:
            out[r["id"]] = {"on": round(inside[0][0], 3), "off": round(inside[-1][1], 3)}
    return out


def measure_sync(media: str | Path, recs: list[dict], segments: list[dict], work_dir: str | Path,
                 source_regions: list[tuple[float, float]] | None = None, planned_track: str | Path | None = None,
                 backend: str = "auto") -> dict:
    """media: the signal to judge (the dubbed speech track, or the final MP4 when it has no music bed)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    wav_path = as_16k_mono(media, work_dir)
    out_regs = speech_regions(wav_path, backend)

    by_id = {s["id"]: s for s in segments}
    placed = [r for r in recs if "placed_start" in r and r["id"] in by_id]
    placed.sort(key=lambda r: r.get("onset", r["placed_start"]))
    edges = unit_edges(out_regs, placed)
    units = []
    for r in placed:
        s, e = by_id[r["id"]], edges.get(r["id"])
        if e is None:
            units.append({"id": r["id"], "missing": True})
            continue
        units.append({"id": r["id"], "start_err": round(e["on"] - s["start"], 3),
                      "end_err": round(e["off"] - s["end"], 3)})
    ok = [u for u in units if not u.get("missing")]
    se = np.array([u["start_err"] for u in ok]) if ok else np.zeros(0)
    ee = np.array([u["end_err"] for u in ok]) if ok else np.zeros(0)
    per_unit = {
        "units": len(units), "missing_voice": sum(bool(u.get("missing")) for u in units),
        "starts_within_0.3s": int((np.abs(se) <= START_TOL).sum()), "starts_within_0.1s": int((np.abs(se) <= 0.1).sum()),
        "ends_within_0.5s": int((np.abs(ee) <= END_TOL).sum()), "ends_within_0.25s": int((np.abs(ee) <= 0.25).sum()),
        "start_err_mean": round(float(se.mean()), 3) if len(se) else None,
        "start_err_abs_mean": round(float(np.abs(se).mean()), 3) if len(se) else None,
        "start_err_max": round(float(se.max()), 3) if len(se) else None,
        "start_err_min": round(float(se.min()), 3) if len(se) else None,
        "end_err_mean": round(float(ee.mean()), 3) if len(ee) else None,
        "end_err_abs_mean": round(float(np.abs(ee).mean()), 3) if len(ee) else None,
        "worst_starts": sorted(ok, key=lambda u: -abs(u["start_err"]))[:5],
        "worst_ends": sorted(ok, key=lambda u: -abs(u["end_err"]))[:5],
    }

    report = {"per_unit": per_unit, "units": units}
    if placed:
        t0 = min(by_id[r["id"]]["start"] for r in placed) - 0.5
        t1 = max(max(by_id[r["id"]]["end"] for r in placed), max(r["placed_end"] for r in placed)) + 0.5
        dub_in = [(a, b) for a, b in out_regs if b > t0 and a < t1]
        if source_regions is not None:
            src_in = [(a, b) for a, b in sorted(source_regions) if b > t0 and a < t1]
            ms, md = _mask(src_in, t0, t1), _mask(dub_in, t0, t1)
            n = min(len(ms), len(md))
            inter, union = (ms[:n] & md[:n]).sum(), (ms[:n] | md[:n]).sum()
            onsets = [a for i, (a, b) in enumerate(src_in) if i == 0 or a - src_in[i - 1][1] >= PHRASE_GAP]
            starts = np.array([a for a, _ in dub_in])
            d = [float(np.min(np.abs(starts - o))) for o in onsets] if len(starts) else []
            report["unit_free"] = {
                "speech_overlap_iou": round(float(inter / max(union, 1)), 3),
                "phrase_onsets": len(onsets),
                "phrase_onsets_matched_0.3s": int(sum(x <= 0.3 for x in d)),
                "phrase_onset_err_abs_mean": round(float(np.mean(d)), 3) if d else None,
            }
        if planned_track:
            report["global_offset_s"] = global_offset(wav_path, planned_track)
    report["measured_on"] = str(media)
    (work_dir / "sync_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def verify_mux(mp4: str | Path, planned_mix: str | Path, work_dir: str | Path) -> dict:
    """Is the audio inside the final MP4 exactly the mix we planned, at the same time? (cross-correlation)

    Together with measuring the speech track itself, this proves sync even when music is under the voice
    (where VAD on the final mix would be disturbed by the music)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    a = as_16k_mono(mp4, work_dir, "mux_output_16k.wav")
    b = as_16k_mono(planned_mix, work_dir, "mux_planned_16k.wav")
    off = global_offset(a, b)
    return {"mp4_vs_planned_mix_offset_s": off, "mux_ok": abs(off) <= 0.02}


def source_regions_from_chunks(chunks: list[dict]) -> list[tuple[float, float]]:
    regs = []
    for c in chunks:
        regs += [(float(a), float(b)) for a, b in (c.get("regions") or [[c.get("vad_start", c["start"]), c.get("vad_end", c["end"])]])]
    return sorted(regs)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    media, dub_json, seg_json, chunks_json, out_dir = sys.argv[1:6]
    planned = sys.argv[6] if len(sys.argv) > 6 else None
    recs = json.loads(Path(dub_json).read_text(encoding="utf-8"))["segments"]
    segs = json.loads(Path(seg_json).read_text(encoding="utf-8"))
    src = source_regions_from_chunks(json.loads(Path(chunks_json).read_text(encoding="utf-8")))
    rep = measure_sync(media, recs, segs, out_dir, src, planned)
    print(json.dumps({k: v for k, v in rep.items() if k != "units"}, indent=2))
