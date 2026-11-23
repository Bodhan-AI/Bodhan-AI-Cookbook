"""Rebuild the non-speech audio bed.

The dub replaces the English speech, but not everything in the original audio is
speech: a lesson has page turns, effects, and sometimes a music bed. Throwing the
whole track away makes the result feel dead, so instead the original is kept and
*gated* — every stretch that the VAD identified as speech is silenced, and
everything else survives at its original level.

The gate uses a short fade at each edge (10 ms) rather than a hard cut, because a
discontinuity in a waveform is an audible click.

Limitation, stated plainly: music that plays *under* speech is removed along with
the speech. Separating the two needs a source-separation model, which is optional
and not enabled by default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import media
from .log import event

PAD_S = 0.08
FADE_S = 0.010


def build_bed(
    video_path: Path,
    segments: list[dict[str, Any]],
    *,
    out_path: Path,
    cache_dir: Path,
    logger: Any,
    sample_rate: int = media.TTS_SAMPLE_RATE,
) -> tuple[np.ndarray, int]:
    """Silence every speech interval and keep the rest, at the dub sample rate."""
    source = _ensure_audio(video_path, cache_dir=cache_dir, sample_rate=sample_rate, logger=logger)
    samples, rate = media.read_wav(source)

    gain = np.ones(len(samples), dtype=np.float32)
    for segment in segments:
        start = float(segment.get("start", 0.0)) - PAD_S
        end = float(segment.get("end", 0.0)) + PAD_S
        _gate(gain, start, end, rate)

    bed = samples * gain
    out_path.parent.mkdir(parents=True, exist_ok=True)
    media.write_wav(out_path, bed, rate)

    removed = float(np.count_nonzero(gain == 0.0)) / max(rate, 1)
    event(
        logger,
        "bed.built",
        stage="assemble",
        source=str(source),
        duration_s=round(len(samples) / rate, 2),
        muted_s=round(removed, 2),
        out=str(out_path),
    )
    return bed, rate


def _gate(gain: np.ndarray, start_s: float, end_s: float, rate: int) -> None:
    """Zero a span of the gain envelope, with linear fades either side."""
    first = max(0, int(round(start_s * rate)))
    last = min(len(gain), int(round(end_s * rate)))
    if last <= first:
        return

    gain[first:last] = 0.0

    fade = max(1, int(round(FADE_S * rate)))
    lo = max(0, first - fade)
    if first > lo:
        ramp = np.linspace(1.0, 0.0, first - lo, dtype=np.float32)
        gain[lo:first] = np.minimum(gain[lo:first], ramp)

    hi = min(len(gain), last + fade)
    if hi > last:
        ramp = np.linspace(0.0, 1.0, hi - last, dtype=np.float32)
        gain[last:hi] = np.minimum(gain[last:hi], ramp)


def _ensure_audio(
    video_path: Path,
    *,
    cache_dir: Path,
    sample_rate: int,
    logger: Any,
) -> Path:
    """Decode the original audio once per input per rate, and reuse it."""
    work = cache_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    digest = media.file_digest(video_path)
    target = work / f"{digest[:16]}.{sample_rate}.wav"
    if target.exists() and target.stat().st_size > 44:
        event(logger, "bed.audio_reused", stage="assemble", path=str(target))
        return target
    media.extract_pcm_wav(video_path, target, sample_rate=sample_rate)
    event(logger, "bed.audio_extracted", stage="assemble", path=str(target))
    return target
