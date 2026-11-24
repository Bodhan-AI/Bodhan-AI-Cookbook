"""Programmatic speech errors on clean synthesized audio with known sentence segments.

segments: [{"start_s", "end_s", "text_span": [a, b]}] (text_span indexes the translation).
Each function returns (new_audio, label) with the label span pointing at the affected words.
"""
from __future__ import annotations

import random

import numpy as np

ANNOTATOR = "injector"


def _idx(t: float, sr: int) -> int:
    return int(round(t * sr))


def truncate(x: np.ndarray, sr: int, segs: list[dict], tr_len: int, rng: random.Random):
    if len(segs) >= 2:
        cut = segs[-1]
        y = x[: _idx(cut["start_s"], sr)]
        span = [cut["text_span"][0], segs[-1]["text_span"][1]]
    else:
        seg = segs[0]
        frac = rng.uniform(0.45, 0.6)
        t = seg["start_s"] + frac * (seg["end_s"] - seg["start_s"])
        y = x[: _idx(t, sr)]
        a, b = seg["text_span"]
        span = [a + int(frac * (b - a)), b]
    return y, {"type": "truncation", "area": "speech", "severity": "critical", "span": span, "annotator": ANNOTATOR, "note": "end of audio cut"}


def drop_segment(x: np.ndarray, sr: int, segs: list[dict], tr_len: int, rng: random.Random):
    if len(segs) < 2:
        seg = segs[0]
        d = seg["end_s"] - seg["start_s"]
        t0, t1 = seg["start_s"] + 0.3 * d, seg["start_s"] + 0.6 * d
        a, b = seg["text_span"]
        span = [a + int(0.3 * (b - a)), a + int(0.6 * (b - a))]
    else:
        k = rng.randrange(len(segs) - 1)  # never the last (that would be truncation)
        seg = segs[k]
        t0, t1 = seg["start_s"], segs[k + 1]["start_s"]
        span = list(seg["text_span"])
    y = np.concatenate([x[: _idx(t0, sr)], x[_idx(t1, sr):]])
    return y, {"type": "speech_omission", "area": "speech", "severity": "major", "span": span, "annotator": ANNOTATOR, "note": "words removed from audio"}


def repeat_segment(x: np.ndarray, sr: int, segs: list[dict], tr_len: int, rng: random.Random):
    seg = rng.choice(segs)
    d = seg["end_s"] - seg["start_s"]
    if len(segs) >= 2 or d < 2.0:
        t0, t1 = seg["start_s"], seg["end_s"]
        span = list(seg["text_span"])
    else:  # repeat the second half of a single long segment
        t0, t1 = seg["start_s"] + 0.5 * d, seg["end_s"]
        a, b = seg["text_span"]
        span = [a + (b - a) // 2, b]
    piece = x[_idx(t0, sr): _idx(t1, sr)]
    gap = np.zeros(int(0.15 * sr), dtype=np.float32)
    at = _idx(t1, sr)
    y = np.concatenate([x[:at], gap, piece, x[at:]])
    return y, {"type": "repetition", "area": "speech", "severity": "major", "span": span, "annotator": ANNOTATOR, "note": "phrase spoken twice"}


def garble(x: np.ndarray, sr: int, segs: list[dict], tr_len: int, rng: random.Random):
    seg = rng.choice(segs)
    d = seg["end_s"] - seg["start_s"]
    a, b = seg["text_span"]
    if rng.random() < 0.5:  # noise burst over the middle of the segment
        t0 = seg["start_s"] + 0.3 * d
        t1 = min(seg["end_s"], t0 + max(1.0, 0.4 * d))
        i0, i1 = _idx(t0, sr), _idx(t1, sr)
        level = float(np.percentile(np.abs(x), 99)) or 0.3
        y = x.copy()
        y[i0:i1] = np.random.default_rng(rng.randrange(1 << 30)).normal(0, level * 0.6, i1 - i0).astype(np.float32)
        span = [a + int(0.3 * (b - a)), a + int(min(1.0, (t1 - seg["start_s"]) / d) * (b - a))]
        note = "noise burst replaces speech"
    else:  # long silence inserted in the middle of the segment
        t = seg["start_s"] + 0.5 * d
        at = _idx(t, sr)
        y = np.concatenate([x[:at], np.zeros(int(rng.uniform(2.0, 3.0) * sr), dtype=np.float32), x[at:]])
        mid = a + (b - a) // 2
        span = [max(a, mid - 8), min(b, mid + 8)]
        note = "long silence inserted mid-sentence"
    return y, {"type": "garbled", "area": "speech", "severity": "major", "span": span, "annotator": ANNOTATOR, "note": note}


SPEECH_INJECTORS = {"truncation": truncate, "speech_omission": drop_segment, "repetition": repeat_segment, "garbled": garble}
