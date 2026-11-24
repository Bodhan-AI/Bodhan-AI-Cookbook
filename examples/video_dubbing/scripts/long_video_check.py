"""Long-video check, free: runs every local (no-API) stage of the pipeline on a long real video and reports the
time and peak memory of each, plus whether the timeline stays exact to the end.

Stages: extract audio; separation gate and a forced full Demucs separation (the worst case: a long video with
music); VAD + chunking (on the separated voice, as the pipeline does); mix + mux with stand-in dub clips (shaped
noise on every speech region, since real clips would need paid TTS); sync measurement of the speech track; MP4
cross-correlation against the planned mix.

usage: python scripts/long_video_check.py <video> <work_dir> [--max-duration S] [--no-separation]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from bodhan_dub.assemble import assemble
from bodhan_dub.audio import extract_audio, probe_media
from bodhan_dub.chunking import chunk_audio
from bodhan_dub.separate import extract_mix, load_model, probe_background, separate
from bodhan_dub.sync_check import measure_sync, source_regions_from_chunks, verify_mux
from bodhan_dub.vad import detect_speech


def rss_bytes() -> int:
    if sys.platform == "win32":
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p          # a 64-bit pseudo-handle, not a C int
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return int(pmc.WorkingSetSize)
    with open("/proc/self/status") as f:
        return next(int(x.split()[1]) * 1024 for x in f if x.startswith("VmRSS:"))


class Stage:
    """Times a block and samples this process's memory every 0.1 s."""
    results: dict[str, dict] = {}

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.peak, self.stop = rss_bytes(), threading.Event()
        self.t0 = time.perf_counter()

        def sample():
            while not self.stop.wait(0.1):
                self.peak = max(self.peak, rss_bytes())
        self.th = threading.Thread(target=sample, daemon=True)
        self.th.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.th.join()
        self.peak = max(self.peak, rss_bytes())
        Stage.results[self.name] = {"seconds": round(time.perf_counter() - self.t0, 1),
                                    "peak_rss_gb": round(self.peak / 2**30, 2)}
        print(f"  {self.name:<22} {Stage.results[self.name]}", flush=True)


def fake_clip(path: Path, dur: float, seed: int) -> None:
    sr = 24000
    t = np.arange(int(dur * sr)) / sr
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t)          # syllable-like loudness changes
    w = np.random.default_rng(seed).standard_normal(len(t)) * 0.15 * env
    sf.write(path, w.astype("float32"), sr, subtype="PCM_16")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("work")
    ap.add_argument("--max-duration", type=float)
    ap.add_argument("--no-separation", action="store_true")
    a = ap.parse_args()
    video, work = Path(a.video), Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    media = probe_media(video)
    total = min(a.max_duration, media["duration"]) if a.max_duration else media["duration"]
    print(f"{video.name}: {total:.1f} s ({total / 60:.1f} min), video codec {media['video_codec']}, "
          f"{os.cpu_count()} CPUs", flush=True)

    with Stage("extract_audio"):
        audio16 = extract_audio(video, work / "audio_16k.wav", duration=total)
    sep = None
    if not a.no_separation:
        with Stage("separation_gate"):
            model = load_model()
            probe = probe_background(extract_mix(video, work / "separate", total), detect_speech(audio16), model)
        print(f"  gate: needed={probe['needed']} gap={probe.get('gap_db')}", flush=True)
        with Stage("separation_full"):
            sep = separate(video, work / "separate", total, model=model)
    timing = Path(sep["vocals_16k_path"]) if sep else audio16
    with Stage("vad_chunking"):
        chunks = chunk_audio(timing, work / "chunks", asr_wav=audio16 if sep else None)
    regions = source_regions_from_chunks([c.__dict__ for c in chunks])
    longest = max(c.duration for c in chunks)

    clips = work / "clips"
    clips.mkdir(exist_ok=True)
    recs, segs = [], []
    for i, (s, e) in enumerate(r for r in regions if r[1] - r[0] >= 0.3):
        p = clips / f"c{i:04d}.wav"
        fake_clip(p, e - s, i)
        recs.append({"id": i, "clip_path": str(p), "placed_start": s, "placed_end": e, "onset": s,
                     "voiced_end_at": e, "text": "परीक्षण वाक्य"})
        segs.append({"id": i, "start": s, "end": e})
    with Stage("mix_mux"):
        asm = assemble(recs, segs, video, work / "out.mp4", work / "assemble",
                       background=sep["background_path"] if sep else None, label="AI-dubbed (Hindi)")
    with Stage("verify_sync"):
        sync = measure_sync(asm["speech_track"], recs, segs, work / "sync", regions, backend="energy")  # stand-ins are noise, not speech
    with Stage("verify_mux"):
        mux = verify_mux(work / "out.mp4", asm["final_audio"], work / "sync")

    pu = sync["per_unit"]
    late = [r for r in recs if r["placed_start"] > total - 60]
    summary = {"video": video.name, "seconds": round(total, 1), "chunks": len(chunks), "longest_chunk_s": round(longest, 1),
               "units": len(recs), "units_in_last_minute": len(late),
               "starts_within_0.1s": f"{pu['starts_within_0.1s']}/{pu['units']}", "start_err_max": pu["start_err_max"],
               "length_error_ms": asm["length_error_ms"], "av_offset_error_ms": asm["av_offset_error_ms"],
               "mux": mux, "speech_lufs": asm["speech_lufs_after"], "video": asm["video"],
               "separation": None if not sep else {k: v for k, v in sep.items() if not k.endswith("_path")},
               "stages": Stage.results}
    (work / "long_video_check.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "stages"}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
