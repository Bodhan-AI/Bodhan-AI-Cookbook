"""Programmatic speech errors on synthesized audio. Labels point at the translation text that is affected.

When the clip was synthesized chunk by chunk (sentence-sized pieces), `chunks` maps each chunk
to its text span and time range, so labels are exact. Without chunks, time is mapped to text
proportionally over the speech region.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from ..audio.analysis import analyze
from ..audio.io import Audio


@dataclass
class AudioInjection:
    audio: Audio
    label: dict
    note: str


def _label(kind: str, severity: str, span: tuple[int, int], audio_span: tuple[float, float]) -> dict:
    return {
        "type": kind,
        "severity": severity,
        "span": [span[0], span[1]],
        "field": "translation",
        "area": "speech",
        "audio_span": [round(audio_span[0], 3), round(audio_span[1], 3)],
        "annotator": "injector",
    }


def _text_at(audio: Audio, text_len: int, t0: float, t1: float) -> tuple[int, int]:
    stats = analyze(audio)
    dur = max(stats.speech_duration, 1e-6)
    a = int(max(t0 - stats.speech_start, 0) / dur * text_len)
    b = int(max(t1 - stats.speech_start, 0) / dur * text_len) + 1
    return (max(0, min(a, text_len - 1)), max(1, min(b, text_len)))


def truncate(audio: Audio, text_len: int, rng: random.Random, chunks: list[dict] | None = None, to_translation=None) -> AudioInjection | None:
    mapper = to_translation or (lambda s, e: (s, e))
    if chunks and len(chunks) >= 2:
        k = rng.randrange(1, len(chunks))
        cut_s = chunks[k]["start_s"]
        span = mapper(chunks[k]["text_start"], chunks[-1]["text_end"])
    else:
        stats = analyze(audio)
        frac = rng.uniform(0.45, 0.8)
        cut_s = stats.speech_start + stats.speech_duration * frac
        span = mapper(*_text_at(audio, text_len, cut_s, audio.duration))
    cut = int(cut_s * audio.rate)
    if cut <= 0 or cut >= len(audio.samples):
        return None
    share = 1 - cut / len(audio.samples)
    return AudioInjection(
        Audio(audio.samples[:cut].copy(), audio.rate),
        _label("speech_omission", "critical" if share > 0.4 else "major", span, (cut_s, audio.duration)),
        f"truncated audio at {cut_s:.2f}s",
    )


def repeat(audio: Audio, text_len: int, rng: random.Random, chunks: list[dict] | None = None, to_translation=None) -> AudioInjection | None:
    mapper = to_translation or (lambda s, e: (s, e))
    if chunks:
        k = rng.randrange(len(chunks))
        t0, t1 = chunks[k]["start_s"], chunks[k]["end_s"]
        span = mapper(chunks[k]["text_start"], chunks[k]["text_end"])
    else:
        stats = analyze(audio)
        length = min(rng.uniform(1.0, 2.0), stats.speech_duration / 2)
        t0 = stats.speech_start + rng.uniform(0, max(stats.speech_duration - length, 0))
        t1 = t0 + length
        span = mapper(*_text_at(audio, text_len, t0, t1))
    a, b = int(t0 * audio.rate), int(t1 * audio.rate)
    if b - a < int(0.3 * audio.rate):
        return None
    seg = audio.samples[a:b]
    samples = np.concatenate([audio.samples[:b], seg, audio.samples[b:]]).astype(np.float32)
    return AudioInjection(Audio(samples, audio.rate), _label("speech_repetition", "major", span, (t1, t1 + (t1 - t0))), f"repeated {t0:.2f}-{t1:.2f}s")


def garble(audio: Audio, text_len: int, rng: random.Random, chunks: list[dict] | None = None, to_translation=None) -> AudioInjection | None:
    mapper = to_translation or (lambda s, e: (s, e))
    stats = analyze(audio)
    if stats.speech_duration < 1.0:
        return None
    kind = rng.choice(["silence", "noise", "clip"])
    t0 = stats.speech_start + stats.speech_duration * rng.uniform(0.25, 0.65)
    a = int(t0 * audio.rate)
    x = audio.samples.copy()
    if kind == "silence":
        length = rng.uniform(2.0, 3.5)
        x = np.concatenate([x[:a], np.zeros(int(length * audio.rate), dtype=np.float32), x[a:]])
        span = mapper(*_text_at(audio, text_len, t0 - 0.3, t0 + 0.3))
        return AudioInjection(
            Audio(x.astype(np.float32), audio.rate), _label("speech_garbled", "major", span, (t0, t0 + length)), f"inserted {length:.1f}s silence"
        )
    length = rng.uniform(0.6, 1.2)
    b = min(a + int(length * audio.rate), len(x))
    span = mapper(*_text_at(audio, text_len, t0, t0 + length))
    if kind == "noise":
        level = float(np.sqrt(np.mean(x**2)) or 0.05) * 3
        noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, level, b - a).astype(np.float32)
        x[a:b] = np.clip(noise, -1, 1)
        return AudioInjection(Audio(x, audio.rate), _label("speech_garbled", "major", span, (t0, t0 + length)), "replaced a stretch with noise")
    peak = float(np.max(np.abs(x)) or 1.0)
    x[a:b] = np.clip(x[a:b] * (8.0 / peak), -1.0, 1.0)
    return AudioInjection(Audio(x, audio.rate), _label("speech_garbled", "major", span, (t0, t0 + length)), "clipped a stretch at 8x gain")


AUDIO_OPS = {"speech_omission": truncate, "speech_repetition": repeat, "speech_garbled": garble}
