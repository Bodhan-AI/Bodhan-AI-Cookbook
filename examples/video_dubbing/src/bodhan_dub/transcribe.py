"""Transcribe each chunk with Indic-Transcribe. A chunk's VAD start/end are its timestamps.

Coverage rule: every chunk ends up either with text or with a logged `skipped` reason.
An empty transcript (music, noise, a cough the VAD let through) is a skip, never
something to dub, so non-speech stretches stay non-speech.
"""

from __future__ import annotations

import io
import logging
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import numpy as np

from .bodhan import BodhanClient, BodhanError
from .chunking import Chunk
from .vad import SAMPLE_RATE

log = logging.getLogger(__name__)


@dataclass
class Segment:
    id: str
    start: float
    end: float
    text: str
    forced_cut: bool = False
    skipped: str | None = None  # reason, when this stretch will not be dubbed

    def to_dict(self) -> dict:
        return asdict(self)


def clip_wav(samples: np.ndarray, start: float, end: float) -> bytes:
    """Cut [start, end) seconds out of 16 kHz float samples as PCM16 WAV bytes."""
    lo, hi = int(round(start * SAMPLE_RATE)), int(round(end * SAMPLE_RATE))
    pcm = (np.clip(samples[lo:hi], -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def transcribe_chunks(client: BodhanClient, samples: np.ndarray, chunks: list[Chunk],
                      *, language: str = "en", max_workers: int = 4) -> list[Segment]:
    def one(index: int, chunk: Chunk) -> Segment:
        seg = Segment(id=f"s{index:04d}", start=chunk.start, end=chunk.end, text="", forced_cut=chunk.forced_cut)
        ctx = {"stage": "transcribe", "segment": seg.id, "start": seg.start, "end": seg.end}
        try:
            seg.text = client.transcribe(clip_wav(samples, chunk.start, chunk.end), language=language)
        except BodhanError as exc:
            # One bad chunk must not sink the video: record why and keep going.
            seg.skipped = f"transcription failed: {exc}"
            log.error("chunk transcription failed", extra={**ctx, "error": str(exc)})
            return seg
        if not seg.text.strip():
            seg.skipped = "no speech recognised"
            log.info("chunk skipped: empty transcript", extra=ctx)
        else:
            log.info("chunk transcribed", extra={**ctx, "chars": len(seg.text), "forced_cut": seg.forced_cut})
        return seg

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # map keeps input order, so ids stay chronological however calls interleave.
        return list(pool.map(one, range(len(chunks)), chunks))
