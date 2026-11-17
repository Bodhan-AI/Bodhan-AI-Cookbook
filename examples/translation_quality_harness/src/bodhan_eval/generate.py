"""Create missing system outputs with Bodhan models (only with --generate)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .audio.io import Audio, concat, decode
from .clients.bodhan import BodhanClient
from .languages import get_language
from .text.speakable import Speakable, chunk_for_tts


@dataclass
class SpeechOutput:
    audio: Audio
    chunks: list[dict]  # {"text_start", "text_end", "start_s", "end_s"} in speakable-text coordinates
    cached: bool
    cost_inr: float


def pick_voice(lang: str, override: str | None = None) -> str:
    if override:
        return override
    voice = get_language(lang).default_voice
    if not voice:
        raise ValueError(f"no indic-speak voice known for {lang!r}; pass --voice")
    return voice


async def synthesize(client: BodhanClient, speakable: Speakable, lang: str, voice: str, style: str | None = None) -> SpeechOutput:
    bounds = chunk_for_tts(speakable)
    if not bounds:
        raise ValueError("nothing speakable in the translation")
    results = await asyncio.gather(*(client.speak(speakable.text[a:b], lang, voice, style) for a, b in bounds))
    clips = [decode(r.value) for r in results]
    chunks, t = [], 0.0
    for (a, b), clip in zip(bounds, clips, strict=True):
        chunks.append({"text_start": a, "text_end": b, "start_s": round(t, 3), "end_s": round(t + clip.duration, 3)})
        t += clip.duration
    return SpeechOutput(concat(clips), chunks, all(r.cached for r in results), sum(r.cost_inr for r in results))
