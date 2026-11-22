"""Indic-Transcribe client: speech-to-text for audio of any length.

The model accepts ~30 s per request, so WAV input is cut into windows (stdlib ``wave``, no ffmpeg) and
the transcripts are joined in order. Other formats are converted to 16 kHz mono WAV with ffmpeg first.
Billed Rs 0.10 per audio minute.
"""

from __future__ import annotations

import hashlib
import os
import wave
from pathlib import Path

import httpx
from pydantic import BaseModel

from bodhan_core.audio import load_wav, split_wav
from bodhan_core.cache import ResultCache
from bodhan_core.client import post
from bodhan_core.config import section
from bodhan_core.errors import ToolError, check_input_file
from bodhan_core.runtime import BACKOFF_S, make_client

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


class TranscribeResult(BaseModel):
    ok: bool
    text: str | None = None
    duration_s: float = 0.0
    segments: list[str] = []
    cost_inr: float = 0.0
    error: ToolError | None = None


def _fail(code: str, message: str, retryable: bool = False) -> TranscribeResult:
    return TranscribeResult(ok=False, error=ToolError(code=code, message=message, retryable=retryable))


async def transcribe(
    audio_path: str,
    language: str | None = None,
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
        windows, duration = split_wav(wav, WINDOW_S)
    except (wave.Error, EOFError) as exc:
        return _fail("bad_audio", f"Could not read the audio as WAV: {exc}. Re-export the file as a standard PCM .wav, or install ffmpeg and pass another format.")
    if duration > MAX_TOTAL_S:
        return _fail("audio_too_long", f"Audio is {duration:.0f}s; the limit per call is {MAX_TOTAL_S}s. Split the file and call again.")

    digest = hashlib.sha256(wav).hexdigest()
    texts: list[str] = []
    billed_s = 0.0
    async with make_client(transport=transport) as client:
        for i, window in enumerate(windows):
            cache_key = (digest, i, language)
            if cache_key in _CACHE:
                texts.append(_CACHE[cache_key])
                continue
            data = {"model": MODEL, "vad": _CFG["vad"]}
            if language:
                data["language"] = language
            res = await post(client, "/v1/audio/transcriptions", key, backoff_s, data=data, files={"file": ("audio.wav", window, "audio/wav")})
            if isinstance(res, ToolError):
                return _fail(res.code, f"Window {i + 1} of {len(windows)} failed ({len(texts)} transcribed before it): {res.message}", res.retryable)
            try:
                text = res.json()["text"].strip()
            except (KeyError, TypeError, ValueError, AttributeError):
                return _fail("bad_response", f"Unexpected response from Bodhan: {res.text[:300]}")
            _CACHE[cache_key] = text
            texts.append(text)
            billed_s += min(WINDOW_S, duration - i * WINDOW_S)

    return TranscribeResult(
        ok=True,
        text=" ".join(t for t in texts if t),
        duration_s=round(duration, 2),
        segments=texts,
        cost_inr=round(billed_s / 60 * INR_PER_MINUTE, 6),
    )
