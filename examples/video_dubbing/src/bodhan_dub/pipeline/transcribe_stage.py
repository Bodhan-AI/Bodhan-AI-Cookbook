"""Stage: transcribe each speech segment with Indic-Transcribe (<=30s per call).

Each segment is a pause-bounded window, so one call per segment stays under the
30s cap and never spans a sentence boundary. An empty transcript means the span
was likely music/noise misdetected as speech — we mark it non-speech so it falls
back to the original audio instead of hallucinating narration.
"""

from __future__ import annotations

import asyncio
import io

import soundfile as sf

from ..ffmpeg import read_wav
from ..logging_setup import get_logger
from ..types import Segment

log = get_logger("transcribe")


def _slice_wav_bytes(samples, sr: int, start: float, end: float) -> bytes:
    a, b = int(start * sr), int(end * sr)
    buf = io.BytesIO()
    sf.write(buf, samples[a:b], sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def transcribe_segments(api, asr_wav: str, segments: list[Segment], cache=None) -> list[Segment]:
    samples, sr = read_wav(asr_wav)

    async def one(seg: Segment):
        clip = _slice_wav_bytes(samples, sr, seg.start, seg.end)
        key = cache.key("asr", clip) if cache else None
        if cache and (hit := cache.get_json(key)) is not None:
            seg.text = hit["text"]
            return
        text = await api.transcribe(clip, seg.duration, lang="en")
        seg.text = text.strip()
        if not seg.text:
            seg.is_speech = False  # silence/music misdetected -> don't dub, keep original
        if cache:
            cache.set_json(key, {"text": seg.text})

    await asyncio.gather(*(one(s) for s in segments))
    kept = [s for s in segments if s.is_speech and s.text]
    log.info("transcribed", extra={"event": "transcribed",
             "extra": {"total": len(segments), "with_speech": len(kept)}})
    return segments
