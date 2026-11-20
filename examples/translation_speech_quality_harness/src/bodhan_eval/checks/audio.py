"""Deterministic audio checks on the waveform: duration, silence, clipping, noise.

No model call. Thresholds are relative to the clip's own loudness where possible, so they
hold across voices, languages and recording levels.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import soundfile as sf

from bodhan_eval.checks.base import CheckResult, Flag
from bodhan_eval.normalize import script_of
from bodhan_eval.params import Params


def load_audio(src: bytes | str | Path) -> tuple[np.ndarray, int]:
    """Mono float32 in [-1, 1] and the sample rate."""
    data, sr = sf.read(
        io.BytesIO(src) if isinstance(src, bytes) else str(src), dtype="float32", always_2d=True
    )
    return data.mean(axis=1), int(sr)


def spoken_chars(text: str) -> int:
    """Letters, marks and digits: a script-agnostic proxy for how much there is to say."""
    return sum(1 for ch in text if ch.isdigit() or script_of(ch))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index ranges where mask is True."""
    if not mask.any():
        return []
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1), strict=False))


def frame_features(x: np.ndarray, sr: int, frame_ms: float) -> tuple[np.ndarray, np.ndarray, int]:
    """Per-frame RMS level (dBFS) and spectral flatness."""
    hop = max(1, int(sr * frame_ms / 1000))
    n = len(x) // hop
    if n == 0:
        return np.zeros(0), np.zeros(0), hop
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    spec = np.abs(np.fft.rfft(frames * np.hanning(hop), axis=1)) ** 2 + 1e-12
    flat = np.exp(np.mean(np.log(spec), axis=1)) / np.mean(spec, axis=1)
    return db, flat, hop


def _text_span_for_time(text: str, t0: float, t1: float, dur: float) -> tuple[int, int] | None:
    """Rough text span for an audio time range, assuming a uniform speaking rate."""
    if not text or dur <= 0:
        return None
    a = int(len(text) * max(0.0, t0) / dur)
    b = int(np.ceil(len(text) * min(dur, t1) / dur))
    return (min(a, len(text)), max(min(b, len(text)), min(a + 1, len(text))))


def check_audio(
    x: np.ndarray,
    sr: int,
    text: str,
    params: Params,
    chars_per_second: float,
) -> CheckResult:
    name = "audio"
    p = params.section("audio")
    dur = len(x) / sr if sr else 0.0
    res = CheckResult(name, signals={"duration_s": round(dur, 3)})
    if dur < 0.05 or not np.isfinite(x).all():
        res.flags.append(
            Flag(
                "speech_garbled",
                "critical",
                name,
                0.95,
                (0, len(text)),
                f"audio is empty or unreadable ({dur:.2f} s)",
                time_span=(0.0, dur),
            )
        )
        return res

    db, flat, hop = frame_features(x, sr, p.get("frame_ms", 20))
    fs = hop / sr  # seconds per frame
    loud = float(np.percentile(db, 90))
    thr = max(p.get("silence_floor_db", -50), loud - p.get("silence_rel_db", 35))
    silent = db < thr
    res.signals.update({"loud_db": round(loud, 1), "silence_threshold_db": round(thr, 1)})

    if silent.all():
        res.flags.append(
            Flag(
                "speech_garbled",
                "critical",
                name,
                0.95,
                (0, len(text)),
                f"audio is silent for its whole {dur:.1f} s",
                time_span=(0.0, dur),
            )
        )
        return res

    voiced_idx = np.flatnonzero(~silent)
    lead = voiced_idx[0] * fs
    trail = (len(silent) - 1 - voiced_idx[-1]) * fs
    speech_s = max(fs, dur - lead - trail)
    res.signals.update(
        {
            "leading_silence_s": round(lead, 3),
            "trailing_silence_s": round(trail, 3),
            "speech_s": round(speech_s, 3),
        }
    )

    # -- silences ----------------------------------------------------------
    edge_max = p.get("max_edge_silence_s", 1.5)
    if lead > edge_max:
        res.flags.append(
            Flag(
                "speech_garbled",
                "minor",
                name,
                0.6,
                (0, min(1, len(text))),
                f"{lead:.1f} s of silence before speech starts",
                time_span=(0.0, round(lead, 2)),
            )
        )
    if trail > edge_max:
        res.flags.append(
            Flag(
                "speech_garbled",
                "minor",
                name,
                0.5,
                (max(0, len(text) - 1), len(text)),
                f"{trail:.1f} s of silence after speech ends",
                time_span=(round(dur - trail, 2), round(dur, 2)),
            )
        )
    gap_max = p.get("max_internal_silence_s", 1.2)
    longest_gap = 0.0
    for s, e in _runs(silent):
        if s == 0 or e == len(silent):
            continue
        gap = (e - s) * fs
        longest_gap = max(longest_gap, gap)
        if gap > gap_max:
            t0, t1 = s * fs, e * fs
            res.flags.append(
                Flag(
                    "speech_garbled",
                    "major",
                    name,
                    0.75,
                    _text_span_for_time(text, t0 - lead, t1 - lead, speech_s),
                    f"{gap:.1f} s silence in the middle of speech at {t0:.1f}–{t1:.1f} s",
                    time_span=(round(t0, 2), round(t1, 2)),
                    details={"gap_s": round(gap, 2)},
                )
            )
    res.signals["longest_internal_silence_s"] = round(longest_gap, 3)

    # -- clipping -----------------------------------------------------------
    clipped = np.abs(x) >= p.get("clip_level", 0.999)
    min_run = p.get("clip_min_run", 3)
    clip_samples = sum(e - s for s, e in _runs(clipped) if e - s >= min_run)
    clip_ratio = clip_samples / len(x)
    res.signals["clip_ratio"] = round(clip_ratio, 5)
    if clip_ratio >= p.get("clip_ratio_minor", 0.001):
        major = clip_ratio >= p.get("clip_ratio_major", 0.01)
        first = int(np.flatnonzero(clipped)[0]) / sr
        res.flags.append(
            Flag(
                "speech_garbled",
                "major" if major else "minor",
                name,
                0.85 if major else 0.6,
                None,
                f"{clip_ratio:.2%} of samples are clipped (distortion), first at {first:.1f} s",
                time_span=(round(first, 2), round(dur, 2)),
                details={"clip_ratio": clip_ratio},
            )
        )

    # -- noise: loud frames whose spectrum is flat (white-ish), sustained ---------
    noisy = (~silent) & (flat > p.get("noise_flatness", 0.45))
    res.signals["noisy_frame_share"] = round(float(noisy[~silent].mean()), 4)
    for s, e in _runs(noisy):
        if (e - s) * fs >= p.get("noise_min_s", 0.4):
            t0, t1 = s * fs, e * fs
            res.flags.append(
                Flag(
                    "speech_garbled",
                    "major",
                    name,
                    0.7,
                    _text_span_for_time(text, t0 - lead, t1 - lead, speech_s),
                    f"noise-like (spectrally flat) audio for {t1 - t0:.1f} s "
                    f"at {t0:.1f}–{t1:.1f} s",
                    time_span=(round(t0, 2), round(t1, 2)),
                )
            )

    # -- duration vs text length -------------------------------------------------
    n_chars = spoken_chars(text)
    if n_chars and chars_per_second > 0:
        expected = n_chars / chars_per_second
        ratio = speech_s / expected
        res.signals.update(
            {
                "expected_s": round(expected, 3),
                "duration_ratio": round(ratio, 3),
                "chars_per_second": round(n_chars / speech_s, 3),
            }
        )
        if ratio < p.get("duration_low", 0.55):
            res.flags.append(
                Flag(
                    "speech_omission",
                    "major",
                    name,
                    0.5,
                    None,
                    f"speech lasts {speech_s:.1f} s but the text should take "
                    f"about {expected:.1f} s "
                    f"({ratio:.0%}); words are probably missing",
                    details={"duration_ratio": ratio},
                )
            )
        elif ratio > p.get("duration_high", 1.8):
            res.flags.append(
                Flag(
                    "speech_repetition",
                    "major",
                    name,
                    0.35,
                    None,
                    f"speech lasts {speech_s:.1f} s, about {ratio:.1f}× the "
                    f"expected {expected:.1f} s; "
                    "possible repetition or stalling",
                    details={"duration_ratio": ratio},
                )
            )
    return res
