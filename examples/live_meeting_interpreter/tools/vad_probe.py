"""Tune the VAD offline: see where utterances would be cut, without calling Bodhan.

    python -m tools.vad_probe --seconds 60            # record what your speakers play (loopback)
    python -m tools.vad_probe --mic --seconds 30      # record your microphone instead
    python -m tools.vad_probe --wav out/probe.wav     # re-run on the last recording
    python -m tools.vad_probe --wav out/probe.wav --end-silence-ms 450 --save-segments
    python -m tools.vad_probe --wav out/probe.wav --sweep

Each recording is saved to out/probe.wav, so you can try many settings on the same audio.
Defaults come from .env (VAD_THRESHOLD, VAD_END_SILENCE_MS, VAD_SOFT_MAX_S, VAD_HARD_MAX_S).
No API keys are needed and nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
from dataclasses import replace
from pathlib import Path
from typing import Callable

import numpy as np
import soxr

from app.audio.capture import AudioSource, find_default_loopback, find_input_device, pyaudio_module
from app.audio.vad import FRAME, Segment, SileroVAD, make_segmenter, replay
from app.audio.wavutil import SAMPLE_RATE, decode_wav, encode_wav
from app.config import VadSettings, load_vad_settings, read_env

OUT = Path("out")
SWEEP_THRESHOLDS = (0.4, 0.5, 0.6)
SWEEP_END_SILENCE_MS = (300, 450, 600, 800, 1000)


async def record(device_kind: str, seconds: float, mic_device: str | None = None) -> np.ndarray:
    pyaudio = pyaudio_module()
    pa = pyaudio.PyAudio()
    try:
        device = find_default_loopback(pa) if device_kind == "loopback" else find_input_device(pa, mic_device)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        source = AudioSource(pa, device, loop, queue)
        print(f"Recording {seconds:.0f} s from {source.name} ...")
        source.start()
        blocks: list[np.ndarray] = []
        end = loop.time() + seconds
        try:
            while loop.time() < end:
                try:
                    blocks.append(await asyncio.wait_for(queue.get(), 0.25))
                except asyncio.TimeoutError:
                    # loopback delivers nothing while nothing plays; keep the timeline honest
                    blocks.append(np.zeros(SAMPLE_RATE // 4, dtype=np.float32))
        finally:
            source.stop()
    finally:
        pa.terminate()
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def load_wav(path: Path) -> np.ndarray:
    audio, rate = decode_wav(path.read_bytes())
    return soxr.resample(audio, rate, SAMPLE_RATE).astype(np.float32) if rate != SAMPLE_RATE else audio


def speech_probabilities(audio: np.ndarray) -> list[float]:
    """Silero output for each full frame. Deterministic for a given recording, so compute once."""
    vad = SileroVAD()
    n = len(audio) // FRAME * FRAME
    return [vad(audio[i:i + FRAME]) for i in range(0, n, FRAME)]


def cached_vad(probs: list[float]) -> Callable[[np.ndarray], float]:
    it = iter(probs)
    return lambda _frame: next(it)


def run(audio: np.ndarray, probs: list[float], v: VadSettings, ptt: bool) -> list[Segment]:
    return replay(audio, lambda clock: make_segmenter(v, push_to_talk=ptt, vad=cached_vad(probs), clock=clock))


def cut_reason(seg: Segment, v: VadSettings, recording_s: float) -> str:
    if seg.ended_at >= recording_s - 1e-6:
        return "end of recording"
    if seg.duration_s >= v.hard_max_s - 0.1:
        return "hard max (no pause found)"
    if seg.duration_s >= v.soft_max_s:
        return "short pause after soft max"
    return "pause"


def summary(segments: list[Segment], recording_s: float) -> dict[str, float]:
    lengths = [s.duration_s for s in segments] or [0.0]
    minutes = max(recording_s / 60, 1e-9)
    return {
        "count": len(segments),
        "median": statistics.median(lengths),
        "max": max(lengths),
        "short": sum(1 for s in segments if s.duration_s < 1.0),
        "audio": sum(s.duration_s for s in segments),
        "per_min": len(segments) / minutes,
    }


def print_segments(segments: list[Segment], v: VadSettings, recording_s: float) -> None:
    print(f"\n{'#':>3}  {'speech at':>9}  {'sent at':>8}  {'audio':>6}  cut by")
    for seg in segments:
        print(f"{seg.seq:>3}  {seg.started_at:>8.2f}s  {seg.ended_at:>7.2f}s  {seg.duration_s:>5.1f}s  "
              f"{cut_reason(seg, v, recording_s)}")


def print_summary(segments: list[Segment], recording_s: float, rpm: int) -> None:
    s = summary(segments, recording_s)
    print(f"\n{s['count']} utterances in {recording_s:.0f} s of audio | median {s['median']:.1f}s, "
          f"max {s['max']:.1f}s, {s['short']} under 1 s")
    print(f"ASR audio billed ~{s['audio']:.0f} s | ~{s['per_min']:.1f} ASR requests/min "
          f"(BODHAN_RPM={rpm}; translation uses the same number on its own key)")


def print_sweep(audio: np.ndarray, probs: list[float], base: VadSettings, ptt: bool, recording_s: float) -> None:
    print(f"\n{'threshold':>9}  {'pause ms':>8}  {'utts':>4}  {'median':>6}  {'max':>5}  {'<1s':>3}  {'req/min':>7}")
    for threshold in SWEEP_THRESHOLDS:
        for end_ms in SWEEP_END_SILENCE_MS:
            v = replace(base, threshold=threshold, end_silence_ms=end_ms)
            s = summary(run(audio, probs, v, ptt), recording_s)
            mark = "  <- current" if (threshold, end_ms) == (base.threshold, base.end_silence_ms) else ""
            print(f"{threshold:>9.2f}  {end_ms:>8}  {s['count']:>4}  {s['median']:>5.1f}s  {s['max']:>4.1f}s  "
                  f"{s['short']:>3}  {s['per_min']:>7.1f}{mark}")
    print("\nPick the shortest pause that doesn't split sentences mid-thought; fewer '<1s' fragments "
          "mean cleaner translations and fewer requests.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline VAD tuning (no Bodhan calls)")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--wav", type=Path, help="analyse an existing WAV instead of recording")
    src.add_argument("--mic", action="store_true", help="record the microphone (default: speaker loopback)")
    parser.add_argument("--seconds", type=float, default=60.0, help="how long to record (default 60)")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--end-silence-ms", type=int)
    parser.add_argument("--soft-max-s", type=float)
    parser.add_argument("--ptt", action="store_true", help="use push-to-talk segmenting (implied by --mic)")
    parser.add_argument("--sweep", action="store_true", help="compare a grid of threshold/pause settings")
    parser.add_argument("--save-segments", action="store_true", help="write each utterance to out/probe_segments/")
    args = parser.parse_args()

    v = load_vad_settings(args.env)
    overrides = {k: val for k, val in (("threshold", args.threshold), ("end_silence_ms", args.end_silence_ms),
                                       ("soft_max_s", args.soft_max_s)) if val is not None}
    v = replace(v, **overrides)
    ptt = args.ptt or args.mic
    env = read_env(args.env)
    rpm = int(env.get("BODHAN_RPM") or 50)

    OUT.mkdir(exist_ok=True)
    if args.wav:
        audio = load_wav(args.wav)
    else:
        audio = asyncio.run(record("mic" if args.mic else "loopback", args.seconds, env.get("MIC_DEVICE") or None))
        (OUT / "probe.wav").write_bytes(encode_wav(audio))
        print(f"Saved {OUT / 'probe.wav'} (re-run with --wav {OUT / 'probe.wav'} to try other settings)")
    recording_s = len(audio) / SAMPLE_RATE
    if recording_s < 1:
        raise SystemExit("Recording is empty. Was anything playing?")
    peak = float(np.abs(audio).max())
    if peak < 0.01:
        print(f"Warning: the recording is near-silent (peak {peak:.4f}). Check the device and volume.")

    probs = speech_probabilities(audio)
    print(f"Settings: threshold={v.threshold} end_silence_ms={v.end_silence_ms} soft_max_s={v.soft_max_s} "
          f"hard_max_s={v.hard_max_s}{' (push-to-talk)' if ptt else ''}")
    segments = run(audio, probs, v, ptt)
    print_segments(segments, v, recording_s)
    print_summary(segments, recording_s, rpm)

    if args.save_segments:
        folder = OUT / "probe_segments"
        folder.mkdir(exist_ok=True)
        for old in folder.glob("*.wav"):
            old.unlink()
        for seg in segments:
            (folder / f"{seg.seq:03d}_{seg.started_at:07.2f}s.wav").write_bytes(encode_wav(seg.audio))
        print(f"Wrote {len(segments)} files to {folder}")

    if args.sweep:
        print_sweep(audio, probs, v, ptt, recording_s)


if __name__ == "__main__":
    main()
