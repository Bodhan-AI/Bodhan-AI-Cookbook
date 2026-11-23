"""Edits of the waveform: the translation is right, the speech is not.

Edits happen at word gaps (quiet stretches between words, ``audio.pauses``), so a cut or a copy never splits a
word and never clicks. The label is placed the way the waveform check places its own findings: through the
speech time map of the *original* clip (``checks.speech.proportional_map``, positions follow speaking time,
pauses excluded), so a stretch of audio maps to the words spoken in it.

    truncate_tail   speech_omission    critical  the last 15-50 % of the speech is gone
    cut_middle      speech_omission    major     1-4 s between two word gaps is gone
    repeat_stretch  speech_repetition  major     0.8-3 s between two word gaps is said twice
    insert_silence  speech_garbled     minor     2-4 s of silence inside the speech
    noise_burst     speech_garbled     major     0.6-1.2 s of loud broadband noise over the speech
    clip_region     speech_garbled     major     1-2 s amplified until it hard-clips
"""

from __future__ import annotations

import random

import numpy as np

from .. import audio as A
from ..checks.speech import TimeMap, proportional_map
from ..models import Item
from .base import Edit, InjectContext

MIN_SECONDS = 3.0  # shorter clips have too few words to cut one stretch and keep the rest recognisable


def _load(it: Item) -> tuple[np.ndarray, int, TimeMap] | None:
    if not it.audio or not it.translation:
        return None
    try:
        x, sr = A.load_audio(it.audio)
    except (OSError, ValueError, RuntimeError):
        return None
    if len(x) < MIN_SECONDS * sr:
        return None
    return x, sr, proportional_map(x, sr, it.translation)


def _gaps(x: np.ndarray, sr: int, lo: float, hi: float) -> list[float]:
    """Centres (seconds) of the word gaps between ``lo`` and ``hi`` (shares of the clip's length)."""
    dur = len(x) / sr
    return [c for c, _ in A.pauses(x, sr, A.WORD_GAP_S) if lo * dur <= c <= hi * dur]


def _stretches(x: np.ndarray, sr: int, min_s: float, max_s: float) -> list[tuple[float, float]]:
    """(start, end) seconds between two word gaps, ``min_s``-``max_s`` long, inside the middle 80 % of the clip."""
    g = _gaps(x, sr, 0.1, 0.9)
    return [(a, b) for i, a in enumerate(g) for b in g[i + 1 :] if min_s <= b - a <= max_s]


def _speech_seconds(x: np.ndarray, sr: int, t0: float, t1: float) -> float:
    active = A.active_mask(x, sr)
    return float(active[int(t0 / A.FRAME_S) : int(t1 / A.FRAME_S)].sum()) * A.FRAME_S


def truncate_tail(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    loaded = _load(it)
    if loaded is None:
        return None
    x, sr, tm = loaded
    total = _speech_seconds(x, sr, 0, len(x) / sr)
    dur = len(x) / sr
    cuts = [g for g in _gaps(x, sr, 0.4, 0.9) if 0.15 <= _speech_seconds(x, sr, g, dur) / total <= 0.5]
    if not cuts:
        return None
    g = rng.choice(cuts)
    return Edit(span=tm.span(g, dur), audio=x[: int(g * sr)].copy(), sr=sr)


def cut_middle(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    loaded = _load(it)
    if loaded is None:
        return None
    x, sr, tm = loaded
    options = _stretches(x, sr, 1.0, 4.0)
    if not options:
        return None
    a, b = rng.choice(options)
    return Edit(span=tm.span(a, b), audio=np.concatenate([x[: int(a * sr)], x[int(b * sr) :]]), sr=sr)


def repeat_stretch(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    loaded = _load(it)
    if loaded is None:
        return None
    x, sr, tm = loaded
    options = _stretches(x, sr, 0.8, 3.0)
    if not options:
        return None
    a, b = rng.choice(options)
    i, j = int(a * sr), int(b * sr)
    return Edit(span=tm.span(a, b), audio=np.concatenate([x[:j], x[i:j], x[j:]]), sr=sr)


def insert_silence(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    loaded = _load(it)
    if loaded is None:
        return None
    x, sr, tm = loaded
    gaps = _gaps(x, sr, 0.2, 0.8)
    if not gaps:
        return None
    g = rng.choice(gaps)
    seconds = round(rng.uniform(2.0, 4.0), 1)
    k = int(g * sr)
    return Edit(span=tm.span(g, g), audio=np.concatenate([x[:k], A.silence(seconds, sr), x[k:]]), sr=sr)


def noise_burst(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    loaded = _load(it)
    if loaded is None:
        return None
    x, sr, tm = loaded
    gaps = _gaps(x, sr, 0.2, 0.8)
    if not gaps:
        return None
    centre, seconds = rng.choice(gaps), round(rng.uniform(0.6, 1.2), 1)
    a, b = int((centre - seconds / 2) * sr), int((centre + seconds / 2) * sr)
    noise = np.random.default_rng(rng.randrange(2**32)).normal(0.0, 0.3 * float(np.max(np.abs(x))), b - a)
    y = x.copy()
    y[a:b] = np.clip(y[a:b] + noise.astype(y.dtype), -1.0, 1.0)
    return Edit(span=tm.span(a / sr, b / sr), audio=y, sr=sr)


def clip_region(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    loaded = _load(it)
    if loaded is None:
        return None
    x, sr, tm = loaded
    options = _stretches(x, sr, 1.0, 2.0)
    if not options:
        return None
    a, b = rng.choice(options)
    i, j = int(a * sr), int(b * sr)
    y = x.copy()
    gain = 0.99 / max(1e-6, 0.25 * float(np.max(np.abs(x[i:j]))))  # a quarter of the peak reaches full scale
    y[i:j] = np.clip(x[i:j] * gain, -0.99, 0.99)
    return Edit(span=tm.span(a, b), audio=y, sr=sr)


ALL = [truncate_tail, cut_middle, repeat_stretch, insert_silence, noise_burst, clip_region]
