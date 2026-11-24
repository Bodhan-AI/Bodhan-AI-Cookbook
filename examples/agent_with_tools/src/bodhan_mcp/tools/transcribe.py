"""Indic-Transcribe client: speech-to-text for audio of any length.

The model accepts ~30 s per request, so WAV input is cut into windows (stdlib ``wave``, no ffmpeg) and
the transcripts are joined in order. Other formats are converted to 16 kHz mono WAV with ffmpeg first.
Billed Rs 0.10 per audio minute.
"""

from __future__ import annotations

import hashlib
import json
import os
import wave
from pathlib import Path

import httpx
from pydantic import BaseModel

from bodhan_core.audio import load_wav, split_wav_timed
from bodhan_core.cache import ResultCache
from bodhan_core.client import post
from bodhan_core.config import section
from bodhan_core.errors import ToolError, check_input_file
from bodhan_core.langid import describe
from bodhan_core.runtime import BACKOFF_S, make_client
from bodhan_core.textio import inline, save_to, save_work

_CFG = section("transcribe")
INR_PER_MINUTE = _CFG["inr_per_minute"]
MODEL = "indic-transcribe"
WINDOW_S = _CFG["window_s"]  # stays under the ~30 s per-request limit
MAX_TOTAL_S = _CFG["max_total_s"]  # hard ceiling so one call cannot create a surprise bill

# bhb/bho are listed for speech-to-text only (scripts/bodhan_api_rules.json).
TRANSCRIBE_LANGUAGES = {
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
}  # fmt: skip

_CACHE: ResultCache[str] = ResultCache()  # (audio hash, window index, language) -> text


class Segment(BaseModel):
    start_s: float
    end_s: float
    text: str


class TranscribeResult(BaseModel):
    ok: bool
    text: str | None = None  # the full transcript, or only its first characters when `text_truncated`
    text_truncated: bool = False  # true: `text` is a short English preview or empty; read the whole transcript from `text_path`
    text_path: str | None = None  # file holding the full transcript (set for long transcripts or when output_path is given)
    segments: list[Segment] = []  # one per audio window with its time range; empty for long transcripts (see segments_path)
    segments_path: str | None = None  # JSON file with all timed segments; the input for `make_subtitles`
    duration_s: float = 0.0
    cost_inr: float = 0.0
    detected_language: str | None = None  # from the transcript's script; null when it has no letters
    script: str | None = None
    language_candidates: list[str] = []  # languages sharing that script (Devanagari: hi, mr, ne, ...)
    error: ToolError | None = None


def _fail(code: str, message: str, retryable: bool = False, cost_inr: float = 0.0) -> TranscribeResult:
    return TranscribeResult(ok=False, cost_inr=cost_inr, error=ToolError(code=code, message=message, retryable=retryable))  # cost_inr: windows already billed


async def transcribe(
    audio_path: str,
    language: str | None = None,
    output_path: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    backoff_s: float = BACKOFF_S,
) -> TranscribeResult:
    path = Path(audio_path)
    if err := check_input_file(path, "audio_path", "audio"):
        return TranscribeResult(ok=False, error=err)
    if language is not None and language not in TRANSCRIBE_LANGUAGES:
        return _fail("unsupported_language", f"`{language}` is not supported. Use one of: {', '.join(sorted(TRANSCRIBE_LANGUAGES))}, or omit it.")
    key = os.environ.get("BODHAN_TRANSCRIBE_KEY")
    if not key:
        return _fail("missing_api_key", "BODHAN_TRANSCRIBE_KEY is not set in the server environment.")

    wav = load_wav(path)
    if isinstance(wav, ToolError):
        return TranscribeResult(ok=False, error=wav)
    try:
        windows, duration = split_wav_timed(wav, WINDOW_S)
    except (wave.Error, EOFError) as exc:
        return _fail("bad_audio", f"Could not read the audio as WAV: {exc}. Re-export the file as a standard PCM .wav, or install ffmpeg and pass another format.")
    if duration > MAX_TOTAL_S:
        return _fail("audio_too_long", f"Audio is {duration:.0f}s; the limit per call is {MAX_TOTAL_S}s. Split the file and call again.")

    digest = hashlib.sha256(wav).hexdigest()
    texts: list[str] = []
    billed_s = 0.0
    starts = [start for start, _ in windows]
    async with make_client(transport=transport) as client:
        for i, (_, window) in enumerate(windows):
            cache_key = (digest, i, language)
            if cache_key in _CACHE:
                texts.append(_CACHE[cache_key])
                continue
            data = {"model": MODEL, "vad": _CFG["vad"]}
            if language:
                data["language"] = language
            res = await post(client, "/v1/audio/transcriptions", key, backoff_s, data=data, files={"file": ("audio.wav", window, "audio/wav")})
            if isinstance(res, ToolError):
                return _fail(res.code, f"Window {i + 1} of {len(windows)} failed ({len(texts)} transcribed before it): {res.message}", res.retryable, round(billed_s / 60 * INR_PER_MINUTE, 6))
            try:
                text = res.json()["text"].strip()
            except (KeyError, TypeError, ValueError, AttributeError):
                return _fail("bad_response", f"Unexpected response from Bodhan: {res.text[:300]}")
            _CACHE[cache_key] = text
            texts.append(text)
            billed_s += (starts[i + 1] if i + 1 < len(starts) else duration) - starts[i]

    full = " ".join(t for t in texts if t)
    ends = starts[1:] + [duration]
    segments = [Segment(start_s=round(a, 2), end_s=round(b, 2), text=t) for a, b, t in zip(starts, ends, texts)]
    segments_path = save_work(json.dumps([s.model_dump() for s in segments], ensure_ascii=False), "segments", ".json")
    text_path, werr = save_to(full, output_path, "transcript")
    if werr:
        return TranscribeResult(ok=False, error=werr, cost_inr=round(billed_s / 60 * INR_PER_MINUTE, 6))
    shown, truncated = inline(full)
    return TranscribeResult(
        ok=True,
        text=shown,
        text_truncated=truncated,
        text_path=text_path,
        segments=[] if truncated else segments,
        segments_path=segments_path,
        duration_s=round(duration, 2),
        cost_inr=round(billed_s / 60 * INR_PER_MINUTE, 6),
        **describe(full),
    )
