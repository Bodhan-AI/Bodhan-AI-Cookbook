"""Local Indic-Transcribe on the GPU: load time, VRAM, speed, and optionally hosted ASR side by side.

    python -m tools.asr_bench out/probe_segments            # every *.wav in a folder (or list files)
    python -m tools.asr_bench out/probe_segments --hosted   # also send each clip to Bodhan's hosted ASR

Weights: `hf download bodhan-ai/indic-transcribe-core --exclude "nemo/*" --local-dir models/indic-transcribe-core`.
The model's own wrapper (indic_transcribe.py in that folder) is imported from the folder.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 16000


def gib(n: float) -> str:
    return f"{n / 2**30:.2f} GiB"


def load_clips(paths: list[Path]) -> list[tuple[str, np.ndarray]]:
    files: list[Path] = []
    for p in paths:
        files.extend(sorted(p.glob("*.wav")) if p.is_dir() else [p])
    clips = []
    for f in files:
        audio, sr = sf.read(f, dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        if sr != SAMPLE_RATE:
            import soxr
            audio = soxr.resample(audio, sr, SAMPLE_RATE).astype(np.float32)
        clips.append((f.name, audio))
    return clips


def run_local(model_dir: Path, clips, lang: str) -> list[tuple[str, float]]:
    import torch

    sys.path.insert(0, str(model_dir.resolve()))
    from indic_transcribe import IndicTranscribe  # type: ignore[import-not-found]

    free0, total = torch.cuda.mem_get_info()
    t0 = time.perf_counter()
    asr = IndicTranscribe.from_pretrained(str(model_dir))
    torch.cuda.synchronize()
    load_s = time.perf_counter() - t0
    free1, _ = torch.cuda.mem_get_info()
    print(f"Loaded on {torch.cuda.get_device_name(0)} in {load_s:.1f}s; "
          f"weights {gib(torch.cuda.memory_allocated())}, GPU in use {gib(total - free1)} of {gib(total)} "
          f"(was {gib(total - free0)})")

    t = time.perf_counter()
    asr(clips[0][1], lang=lang)  # warm-up: CUDA kernels, allocator
    torch.cuda.synchronize()
    print(f"Warm-up call {time.perf_counter() - t:.2f}s\n")

    torch.cuda.reset_peak_memory_stats()
    results = []
    for name, audio in clips:
        t = time.perf_counter()
        text = asr(audio, lang=lang)
        torch.cuda.synchronize()
        results.append((text, time.perf_counter() - t))
    free2, _ = torch.cuda.mem_get_info()
    print(f"Peak allocated while transcribing {gib(torch.cuda.max_memory_allocated())}; "
          f"GPU in use now {gib(total - free2)} of {gib(total)}\n")
    return results


async def run_hosted(clips, lang: str) -> list[tuple[str, float]]:
    from app.audio.wavutil import encode_wav
    from app.bodhan.client import BodhanClient
    from app.config import load_settings

    client = BodhanClient(load_settings())
    out = []
    try:
        for _, audio in clips:
            t = time.perf_counter()
            text = await client.transcribe(encode_wav(audio), lang)
            out.append((text, time.perf_counter() - t))
    finally:
        await client.aclose()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local Indic-Transcribe on the GPU")
    parser.add_argument("audio", nargs="+", type=Path, help="WAV files or folders of WAVs")
    parser.add_argument("--model", type=Path, default=Path("models/indic-transcribe-core"))
    parser.add_argument("--lang", default="hi")
    parser.add_argument("--hosted", action="store_true", help="also run Bodhan's hosted ASR (billed per second)")
    args = parser.parse_args()

    clips = load_clips(args.audio)
    if not clips:
        sys.exit("No WAV files found.")
    local = run_local(args.model, clips, args.lang)
    hosted = asyncio.run(run_hosted(clips, args.lang)) if args.hosted else None

    rtf = []
    for i, (name, audio) in enumerate(clips):
        dur = len(audio) / SAMPLE_RATE
        text, secs = local[i]
        rtf.append(secs / dur)
        print(f"{name}  {dur:4.1f}s audio | local {secs:.2f}s" +
              (f" | hosted {hosted[i][1]:.2f}s" if hosted else ""))
        print(f"   local : {text}")
        if hosted:
            print(f"   hosted: {hosted[i][0]}")
    times = [s for _, s in local]
    print(f"\nLocal: median {statistics.median(times):.2f}s, max {max(times):.2f}s per clip; "
          f"median {statistics.median(rtf):.3f}s of work per second of audio")
    if hosted:
        print(f"Hosted: median {statistics.median(s for _, s in hosted):.2f}s per clip (incl. network)")


if __name__ == "__main__":
    main()
