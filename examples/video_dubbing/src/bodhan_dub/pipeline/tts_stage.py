"""Stage: synthesize target speech and fit it to each segment's time slot.

Drives Indic-Speak, measures the result, and applies the duration-fit ladder from
fit.py: pad -> stretch -> condense-English+re-translate+re-synth -> accept overflow.
Long translations are split on sentence boundaries (the hosted TTS prefers short
inputs) and concatenated, so chunking never breaks a sentence.
"""

from __future__ import annotations

import io
import os
import re

import numpy as np
import soundfile as sf

from ..ffmpeg import read_wav, time_stretch, write_wav
from ..logging_setup import get_logger
from ..types import DubSegment
from .fit import plan_fit
from .translate_stage import translate_one

log = get_logger("tts")

MAX_TTS_CHARS = 280
_SENT_SPLIT = re.compile(r"(?<=[.!?।॥])\s+")


def split_for_tts(text: str, limit: int = MAX_TTS_CHARS) -> list[str]:
    """Split into <=limit-char chunks on sentence boundaries, never mid-sentence."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks, cur = [], ""
    for sent in _SENT_SPLIT.split(text):
        if not sent:
            continue
        if len(cur) + len(sent) + 1 <= limit:
            cur = f"{cur} {sent}".strip()
        else:
            if cur:
                chunks.append(cur)
            # a single over-long sentence: hard-wrap on whitespace as a last resort
            while len(sent) > limit:
                cut = sent.rfind(" ", 0, limit) or limit
                chunks.append(sent[:cut].strip())
                sent = sent[cut:].strip()
            cur = sent
    if cur:
        chunks.append(cur)
    return chunks


async def _synthesize(api, text: str, voice: str, lang: str, style: str, work_sr: int) -> np.ndarray:
    """Synthesize (possibly multi-chunk) and return a single mono float array."""
    parts: list[np.ndarray] = []
    for chunk in split_for_tts(text):
        wav_bytes = await api.speak(chunk, voice, lang, style)
        data, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
        if data.ndim == 2:
            data = data.mean(axis=1)
        if sr != work_sr and len(data):
            # cheap linear resample to the working rate (keeps deps compile-free)
            idx = np.linspace(0, len(data) - 1, int(len(data) * work_sr / sr))
            data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        parts.append(data)
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)


async def synth_and_fit(
    api, brain, dub: DubSegment, lang: str, voice: str, settings, segdir: str,
    work_sr: int = 24000, cache=None, glossary_hint: str = "",
) -> DubSegment:
    """Produce a fitted per-segment WAV and record the fit decision on `dub`."""
    if dub.status == "skipped" or not dub.translation.strip():
        return dub

    dub.voice = voice
    english = dub.source_text
    slot = dub.duration
    raw_path = os.path.join(segdir, f"seg_{dub.index:04d}.raw.wav")
    out_path = os.path.join(segdir, f"seg_{dub.index:04d}.wav")

    for attempt in range(settings.max_condense_rounds + 1):
        audio = await _synthesize(api, dub.translation, voice, lang, dub.style, work_sr)
        dur = len(audio) / work_sr if work_sr else 0.0
        can_condense = attempt < settings.max_condense_rounds
        plan = plan_fit(slot, dur, min_stretch=settings.min_stretch,
                        max_stretch=settings.max_stretch, can_condense=can_condense)

        if plan.action != "condense":
            write_wav(raw_path, audio, work_sr)
            time_stretch(raw_path, out_path, plan.speed)
            if os.path.exists(raw_path):
                os.remove(raw_path)  # intermediate; keep only the fitted per-segment WAV
            fitted, _ = read_wav(out_path)
            dub.audio_path = out_path
            dub.audio_duration = round(len(fitted) / work_sr, 3)
            dub.stretch = round(plan.speed, 4)
            dub.fit_action = plan.action
            dub.status = "dubbed"
            log.info("fitted segment", extra={"lang": lang, "seg": dub.index,
                     "event": "fit", "extra": {"action": plan.action,
                     "slot_s": round(slot, 2), "audio_s": round(dur, 2),
                     "speed": dub.stretch, "condense_rounds": dub.condense_rounds}})
            return dub

        # condense the English side, re-translate via Bodhan, try again
        dub.condense_rounds += 1
        english = await brain.condense(english, plan.condense_ratio, keep_hint=glossary_hint)
        dub.translation, _ = await translate_one(api, english, lang, dub.script, cache)

    return dub  # unreachable: last loop iteration always finalizes (can_condense False)
