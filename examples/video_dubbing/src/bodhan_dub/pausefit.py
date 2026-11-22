"""Pause-aware clip fitting: trim edge silence, shrink/widen pauses inside a TTS clip, then time-stretch.

Changing pauses moves the dub toward its slot without speeding up or slowing down the words.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .stretch import apply_speed

FRAME = 0.01          # analysis frame (s)
VOICED_DB = -40.0     # a frame is voiced if within 40 dB of the clip's loudest frame
MIN_PAUSE = 0.12      # internal silences shorter than this are not pauses (just gaps between sounds)
PAUSE_FLOOR = 0.10    # never shrink a pause below this
MAX_ADD = 0.6         # never widen a single pause by more than this
LEAD_KEEP = 0.03      # silence kept before the first sound
TAIL_KEEP = 0.03      # silence kept after the last sound


def analyze(wav: np.ndarray, sr: int) -> dict:
    """Voiced span and internal pauses of a mono clip (times in seconds)."""
    wav = wav if wav.ndim == 1 else wav.mean(axis=1)
    hop = max(1, int(sr * FRAME))
    n = int(np.ceil(len(wav) / hop))
    if n == 0:
        return {"lead": 0.0, "voiced_end": 0.0, "voiced": 0.0, "pauses": [], "max_shrink": 0.0, "n_pauses": 0,
                "length": 0.0}
    padded = np.pad(wav, (0, n * hop - len(wav)))
    db = 10 * np.log10((padded.reshape(n, hop).astype("float64") ** 2).mean(axis=1) + 1e-12)
    voiced = db > db.max() + VOICED_DB
    idx = np.where(voiced)[0]
    if len(idx) == 0:
        return {"lead": 0.0, "voiced_end": 0.0, "voiced": 0.0, "pauses": [], "max_shrink": 0.0, "n_pauses": 0,
                "length": len(wav) / sr}
    f0, f1 = int(idx[0]), int(idx[-1])
    pauses, run = [], None
    for f in range(f0, f1 + 1):
        if not voiced[f] and run is None:
            run = f
        elif voiced[f] and run is not None:
            if (f - run) * FRAME >= MIN_PAUSE:
                pauses.append((run * FRAME, f * FRAME))
            run = None
    lead, vend = f0 * FRAME, min((f1 + 1) * FRAME, len(wav) / sr)
    return {"lead": round(lead, 3), "voiced_end": round(vend, 3), "voiced": round(vend - lead, 3),
            "pauses": [(round(a, 3), round(b, 3)) for a, b in pauses],
            "max_shrink": round(sum(max(0.0, (b - a) - PAUSE_FLOOR) for a, b in pauses), 3),
            "n_pauses": len(pauses), "length": round(len(wav) / sr, 3)}


def analyze_file(path: str | Path) -> dict:
    wav, sr = sf.read(str(path), dtype="float32")
    return analyze(wav, sr)


def adjust_pauses(wav: np.ndarray, sr: int, pauses: list[tuple[float, float]], delta: float) -> tuple[np.ndarray, float]:
    """Shrink (delta < 0) or widen (delta > 0) the given pauses by |delta| seconds in total.

    Samples are removed from / zeros inserted at the middle of each pause (inside silence, so no clicks).
    Returns (new wav, seconds actually changed: negative = removed).
    """
    if not pauses or abs(delta) < 1e-3:
        return wav, 0.0
    lens = [b - a for a, b in pauses]
    if delta < 0:
        room = [max(0.0, x - PAUSE_FLOOR) for x in lens]
        total = sum(room)
        if total <= 0:
            return wav, 0.0
        take = min(-delta, total)
        change = [-take * r / total for r in room]
    else:
        total = sum(lens)
        change = [min(MAX_ADD, delta * x / total) for x in lens]
    pieces, last = [], 0
    for (a, b), ch in zip(pauses, change):
        mid = int(round((a + b) / 2 * sr))
        n = int(round(abs(ch) * sr))
        if ch < 0:
            pieces.append(wav[last : max(last, mid - n // 2)])
            last = mid - n // 2 + n
        else:
            pieces.append(wav[last:mid])
            pieces.append(np.zeros(n, dtype=wav.dtype))
            last = mid
    pieces.append(wav[last:])
    return np.concatenate(pieces), round(sum(change), 3)


def render_clip(src: str | Path, dst: str | Path, pause_delta: float, speed: float,
                lead_keep: float = LEAD_KEEP, tail_keep: float = TAIL_KEEP) -> dict:
    """Trim edge silence, adjust pauses, time-stretch. Returns measured facts about the final clip:
    {dur, lead (s of silence before the first sound), voiced_end, voiced, pause_change}."""
    src, dst = Path(src), Path(dst)
    wav, sr = sf.read(str(src), dtype="float32")
    wav = wav if wav.ndim == 1 else wav.mean(axis=1)
    info = analyze(wav, sr)
    a = max(0, int(round((info["lead"] - lead_keep) * sr)))
    b = min(len(wav), int(round((info["voiced_end"] + tail_keep) * sr)))
    cut = wav[a:b] if b > a else wav
    off = a / sr
    cut, changed = adjust_pauses(cut, sr, [(p0 - off, p1 - off) for p0, p1 in info["pauses"]], pause_delta)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".pre.wav")
    sf.write(str(tmp), cut, sr, subtype="PCM_16")
    apply_speed(tmp, dst, speed)
    tmp.unlink(missing_ok=True)
    out = analyze_file(dst)
    return {"dur": out["length"], "lead": out["lead"], "voiced_end": out["voiced_end"], "voiced": out["voiced"],
            "pause_change": changed}
