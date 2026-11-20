"""Audio error injections on a synthesized or supplied recording.

Text spans for speech labels are approximate (proportional to time), so each label says
`span_approx: true`; the time span is exact."""

from __future__ import annotations

import random
from typing import Any

import numpy as np

from bodhan_eval.checks.audio import frame_features


def _quiet_point(x: np.ndarray, sr: int, center: int, radius_s: float = 0.3) -> int:
    """The quietest 20 ms frame within ±radius of `center` (cuts land in pauses)."""
    r = int(radius_s * sr)
    lo, hi = max(0, center - r), min(len(x), center + r)
    db, _, hop = frame_features(x[lo:hi], sr, 20)
    return lo + int(np.argmin(db)) * hop if len(db) else center


def _label(cat, sev, text_len, t0, t1, dur, kind, **extra) -> dict[str, Any]:
    a = int(text_len * max(0.0, t0) / dur) if dur else 0
    b = int(np.ceil(text_len * min(dur, t1) / dur)) if dur else text_len
    return {
        "type": cat,
        "area": "speech",
        "severity": sev,
        "span": [min(a, text_len), max(min(b, text_len), min(a + 1, text_len))],
        "time_span": [round(t0, 2), round(t1, 2)],
        "span_approx": True,
        "annotator": "injector",
        "injection": kind,
        **extra,
    }


def truncate(x, sr, text_len, rng: random.Random):
    keep = rng.uniform(0.45, 0.75)
    cut = _quiet_point(x, sr, int(len(x) * keep))
    dur = len(x) / sr
    sev = "critical" if cut / len(x) < 0.6 else "major"
    return x[:cut], _label("speech_omission", sev, text_len, cut / sr, dur, dur, "truncate")


def repeat(x, sr, text_len, rng: random.Random):
    a = _quiet_point(x, sr, int(len(x) * rng.uniform(0.25, 0.55)))
    b = _quiet_point(x, sr, min(len(x) - 1, a + int(len(x) * rng.uniform(0.12, 0.22))))
    if b - a < int(0.3 * sr):
        return None
    dur = len(x) / sr
    out = np.concatenate([x[:b], x[a:b], x[b:]])
    return out, _label(
        "speech_repetition", "major", text_len, a / sr, b / sr, dur, "repeat_segment"
    )


def silence(x, sr, text_len, rng: random.Random):
    p = _quiet_point(x, sr, int(len(x) * rng.uniform(0.3, 0.7)))
    gap = rng.uniform(2.0, 3.0)
    dur = len(x) / sr
    out = np.concatenate([x[:p], np.zeros(int(gap * sr), x.dtype), x[p:]])
    lab = _label("speech_garbled", "major", text_len, p / sr, p / sr, dur, "insert_silence")
    lab["time_span"] = [round(p / sr, 2), round(p / sr + gap, 2)]
    return out, lab


def noise(x, sr, text_len, rng: random.Random):
    dur = len(x) / sr
    length = min(dur * 0.4, rng.uniform(1.0, 1.5))
    t0 = rng.uniform(0.2, max(0.21, dur - length - 0.2))
    a, b = int(t0 * sr), int((t0 + length) * sr)
    level = float(np.sqrt(np.mean(x**2)) + 1e-4)
    out = x.copy()
    gen = np.random.default_rng(rng.randrange(2**32))
    out[a:b] = np.clip(out[a:b] + gen.normal(0, 2.5 * level, b - a).astype(x.dtype), -1, 1)
    return out, _label("speech_garbled", "major", text_len, a / sr, b / sr, dur, "add_noise")


AUDIO = {"truncate": truncate, "repeat": repeat, "silence": silence, "noise": noise}
