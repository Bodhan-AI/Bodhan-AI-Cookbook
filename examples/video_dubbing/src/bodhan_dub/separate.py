"""Split the source audio into voice and background with Demucs (htdemucs, a local model on CPU).

Why: speech timing (VAD, edges, pauses) is measured on the clean voice, so music or noise under the speaker
cannot move it; and the background (music, effects, room tone) is kept under the dubbed voice instead of
being muted. This is not ASR, translation or TTS (those stay Bodhan models).
Results are cached by the hash of the input audio, so a re-run costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .audio import extract_audio

log = logging.getLogger(__name__)

MODEL = "htdemucs"
SR = 44_100
BLOCK = 30.0     # seconds separated at a time (keeps memory bounded on long videos)
CONTEXT = 2.0    # extra audio on each side of a block, thrown away after separation
VERSION = 1      # bump when the output format changes (invalidates the cache)


def _sha(path: Path, extra: str = "") -> str:
    h = hashlib.sha256(extra.encode())
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:20]


def load_model(name: str = MODEL):
    from demucs.pretrained import get_model

    m = get_model(name)
    m.eval()
    return m


def _demucs_apply(model, x):
    from demucs.apply import apply_model

    return apply_model(model, x, shifts=0, split=True, overlap=0.25, progress=False)  # shifts=0: deterministic


def separate_array(mix: np.ndarray, model, block: float = BLOCK, context: float = CONTEXT,
                   apply_fn: Callable | None = None) -> tuple[np.ndarray, np.ndarray]:
    """mix: (n, channels) float32 at model.samplerate. Returns (vocals, background), same shape as mix.

    Background = sum of the non-vocal stems (Demucs' own two-stem convention).
    """
    import torch

    # silero-vad calls torch.set_num_threads(1) at import time; without this Demucs would run on one core.
    prev_threads = torch.get_num_threads()
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    try:
        return _separate_blocks(mix, model, block, context, apply_fn or _demucs_apply)
    finally:
        torch.set_num_threads(prev_threads)


def _separate_blocks(mix: np.ndarray, model, block: float, context: float, apply_fn: Callable):
    import torch

    x = torch.from_numpy(np.ascontiguousarray(mix.T, dtype="float32"))
    ref = x.mean(0)
    mu, sd = float(ref.mean()), float(ref.std()) + 1e-8   # whole-file normalisation, same for every block
    x = (x - mu) / sd
    n, sr = x.shape[1], model.samplerate
    B, C = max(1, int(block * sr)), int(context * sr)
    vi = list(model.sources).index("vocals")
    voc = np.zeros(mix.shape, dtype="float32")
    bg = np.zeros(mix.shape, dtype="float32")
    with torch.no_grad():
        for a in range(0, n, B):
            b = min(n, a + B)
            lo, hi = max(0, a - C), min(n, b + C)
            est = apply_fn(model, x[None, :, lo:hi])[0]          # (sources, channels, time)
            est = est[:, :, a - lo : b - lo] * sd
            v = est[vi]
            o = est.sum(0) - v
            voc[a:b] = (v + mu).numpy().T
            bg[a:b] = o.numpy().T
    return voc, bg


def _db(a: np.ndarray) -> float:
    return round(float(20 * np.log10(np.sqrt(np.mean(np.square(a, dtype="float64"))) + 1e-9)), 1)


# --------------------------------------------------------------------------- gate: is separation needed?
GATE_WINDOWS = 4      # sample windows ...
GATE_LEN = 8.0        # ... of this many seconds, placed where there is speech
CLEAN_DB = 30.0       # background at least this far below the voice = clean, full separation is skipped


def pick_windows(regions: list[tuple[float, float]], total: float, k: int = GATE_WINDOWS,
                 length: float = GATE_LEN) -> list[tuple[float, float]]:
    """k windows centred at evenly spaced points of the SPEECH timeline (so each one contains speech)."""
    regs = sorted((max(0.0, a), min(total, b)) for a, b in regions if b > a)
    speech = sum(b - a for a, b in regs)
    if speech <= 0:
        centres = [(i + 0.5) / k * total for i in range(k)]
    else:
        centres = []
        for i in range(k):
            t, acc = (i + 0.5) / k * speech, 0.0
            for a, b in regs:
                if acc + (b - a) >= t:
                    centres.append(a + (t - acc))
                    break
                acc += b - a
    out: list[tuple[float, float]] = []
    for c in centres:
        a = min(max(0.0, c - length / 2), max(0.0, total - length))
        b = min(total, a + length)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((round(a, 3), round(b, 3)))
    return out


def extract_mix(video: str | Path, out_dir: str | Path, duration: float | None = None) -> Path:
    """Container-aligned 44.1 kHz stereo copy of the source audio (what Demucs works on)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return extract_audio(video, out_dir / "mix_44k.wav", sample_rate=SR, channels=2, duration=duration)


def probe_background(mix_path: str | Path, regions: list[tuple[float, float]], model=None,
                     apply_fn: Callable | None = None, clean_db: float = CLEAN_DB) -> dict:
    """Separate only a few short speech windows and compare voice vs background level.

    Returns {"needed": bool, "voice_to_background_db": float, ...}. Cheap (about 30 s of audio).
    """
    mix, sr = sf.read(str(mix_path), dtype="float32", always_2d=True)
    windows = pick_windows(regions, len(mix) / sr)
    t0 = time.time()
    model = model or load_model()
    vs, bs = [], []
    for a, b in windows:
        seg = mix[int(a * sr) : int(b * sr)]
        if len(seg) < sr // 2:
            continue
        v, bg = separate_array(seg, model, apply_fn=apply_fn)
        vs.append(v)
        bs.append(bg)
    if not vs:
        return {"needed": False, "reason": "no audio to probe", "windows": windows}
    v, bg = np.concatenate(vs), np.concatenate(bs)
    ratio = round(_db(v) - _db(bg), 1)
    info = {"needed": bool(ratio < clean_db), "voice_db": _db(v), "background_db": _db(bg),
            "voice_to_background_db": ratio, "clean_db": clean_db, "windows": windows,
            "seconds": round(time.time() - t0, 1)}
    log.info("separate: probe voice %.1f dB, background %.1f dB (%.1f dB apart) -> %s",
             info["voice_db"], info["background_db"], ratio, "separate" if info["needed"] else "clean, skip")
    return info


def separate(video: str | Path, out_dir: str | Path, duration: float | None = None,
             model_name: str = MODEL, model=None, apply_fn: Callable | None = None) -> dict:
    """Write mix_44k.wav, vocals_44k.wav, background_44k.wav (stereo, container timeline) and vocals_16k.wav
    (mono, for VAD/ASR) into out_dir. Returns the info dict (also saved as separation.json)."""
    out_dir = Path(out_dir)
    mix_path = extract_mix(video, out_dir, duration)
    key = _sha(mix_path, f"{model_name}|{VERSION}")
    paths = {"mix": mix_path, "vocals": out_dir / "vocals_44k.wav", "background": out_dir / "background_44k.wav",
             "vocals_16k": out_dir / "vocals_16k.wav"}
    meta_path = out_dir / "separation.json"
    if meta_path.exists() and all(p.exists() for p in paths.values()):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("key") == key:
            log.info("separate: cached (%s)", meta_path)
            return {**meta, "cached": True}

    mix, sr = sf.read(str(mix_path), dtype="float32", always_2d=True)
    t0 = time.time()
    model = model or load_model(model_name)
    t_load = time.time() - t0
    if model.samplerate != sr:
        raise ValueError(f"model expects {model.samplerate} Hz, got {sr}")
    t1 = time.time()
    voc, bg = separate_array(mix, model, apply_fn=apply_fn)
    t_sep = time.time() - t1
    sf.write(str(paths["vocals"]), voc, sr, subtype="FLOAT")
    sf.write(str(paths["background"]), bg, sr, subtype="FLOAT")
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(paths["vocals"]),
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(paths["vocals_16k"])],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-300:]}")
    secs = len(mix) / sr
    meta = {"key": key, "model": model_name, "seconds_audio": round(secs, 2), "seconds_load": round(t_load, 1),
            "seconds_separate": round(t_sep, 1), "real_time_factor": round(t_sep / max(secs, 1e-6), 3),
            "mix_db": _db(mix), "vocals_db": _db(voc), "background_db": _db(bg),
            **{f"{k}_path": str(p) for k, p in paths.items()}}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("separate: %.0fs audio in %.0fs (x%.2f real time); vocals %.1f dB, background %.1f dB",
             secs, t_sep, meta["real_time_factor"], meta["vocals_db"], meta["background_db"])
    return {**meta, "cached": False}


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    info = separate(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(json.dumps(info, indent=2))
