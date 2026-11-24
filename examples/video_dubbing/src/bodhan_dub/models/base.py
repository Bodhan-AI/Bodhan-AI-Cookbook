"""Shared HTTP plumbing for the three Bodhan models.

One class, three methods (transcribe / translate / speak), each going to the
matching Bodhan endpoint with its own key. Cross-cutting concerns live here:
retries with exponential backoff, a shared concurrency cap, cost accounting, and
a --mock mode that returns deterministic fixtures so the whole pipeline (and the
test suite) runs offline with no keys or credit.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import struct
from dataclasses import dataclass, field

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ..logging_setup import get_logger

log = get_logger("bodhan-api")

# Hosted pricing (INR).
INR_PER_TRANSCRIBE_MIN = 0.10
INR_PER_TRANSLATE_10K_TOK = 0.20
INR_PER_SPEAK_10K_CHAR = 6.0

RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)

_backoff = wait_exponential_jitter(max=30)


def _retry_after_wait(retry_state):
    """Honour a 429 Retry-After header when present, else exponential backoff.

    Precise server-directed waits recover from tight rate limits far better than
    blind backoff when many calls are retrying at once.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        ra = exc.response.headers.get("retry-after")
        if ra:
            try:
                return min(60.0, float(ra))
            except ValueError:
                pass
    return _backoff(retry_state)


@dataclass
class CostMeter:
    """Accumulates spend so report.json can show where credit went."""

    transcribe_seconds: float = 0.0
    translate_tokens: int = 0
    speak_chars: int = 0
    calls: dict = field(default_factory=lambda: {"transcribe": 0, "translate": 0, "speak": 0})

    def add_transcribe(self, seconds: float) -> None:
        self.transcribe_seconds += seconds
        self.calls["transcribe"] += 1

    def add_translate(self, tokens: int) -> None:
        self.translate_tokens += tokens
        self.calls["translate"] += 1

    def add_speak(self, chars: int) -> None:
        self.speak_chars += chars
        self.calls["speak"] += 1

    @property
    def inr(self) -> dict:
        t = (self.transcribe_seconds / 60.0) * INR_PER_TRANSCRIBE_MIN
        tr = (self.translate_tokens / 10_000.0) * INR_PER_TRANSLATE_10K_TOK
        sp = (self.speak_chars / 10_000.0) * INR_PER_SPEAK_10K_CHAR
        return {
            "transcribe": round(t, 4),
            "translate": round(tr, 4),
            "speak": round(sp, 4),
            "total": round(t + tr + sp, 4),
        }


def _pcm_wav(samples: bytes, sr: int, nchan: int = 1) -> bytes:
    """Wrap PCM16 bytes in a minimal WAV container (used by the mock synth)."""
    byte_rate = sr * nchan * 2
    block_align = nchan * 2
    header = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, nchan, sr, byte_rate, block_align, 16)
    header += b"data" + struct.pack("<I", len(samples))
    return header + samples


def _mock_tone(duration_s: float, sr: int = 24000, freq: float = 180.0) -> bytes:
    """Deterministic low-amplitude tone of a given length -> realistic WAV bytes.

    Length is what the fit logic actually consumes, so mock TTS can faithfully
    exercise duration-drift handling without a real voice.
    """
    n = max(1, int(duration_s * sr))
    buf = bytearray()
    for i in range(n):
        v = int(3000 * math.sin(2 * math.pi * freq * (i / sr)))
        buf += struct.pack("<h", v)
    return _pcm_wav(bytes(buf), sr)


class RateLimiter:
    """Paces request *starts* and adapts to the server's limit.

    Hosted Bodhan keys enforce a small per-model token bucket (empirically ~a dozen
    calls then 429). We space out request starts by `interval` seconds and, on a 429,
    multiplicatively increase the interval so the pipeline self-tunes below the limit
    instead of starving its own retries with a burst of competing calls.
    """

    def __init__(self, interval: float = 1.0, max_interval: float = 20.0):
        self.interval = max(0.0, interval)
        self.max_interval = max_interval
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait:
            await asyncio.sleep(wait)

    def penalize(self) -> None:
        self.interval = min(self.max_interval, max(0.5, self.interval) * 1.5)


def make_limiters(interval: float) -> dict:
    return {m: RateLimiter(interval) for m in ("transcribe", "translate", "speak")}


class BodhanAPI:
    def __init__(self, settings, semaphore: asyncio.Semaphore, cost: CostMeter, limiters: dict | None = None):
        self.s = settings
        self.sem = semaphore
        self.cost = cost
        self.limiters = limiters or {}
        self._client: httpx.AsyncClient | None = None

    async def _pace(self, model: str) -> None:
        lim = self.limiters.get(model)
        if lim:
            await lim.acquire()

    def _throttled(self, model: str) -> None:
        lim = self.limiters.get(model)
        if lim:
            lim.penalize()

    async def __aenter__(self):
        if not self.s.mock:
            self._client = httpx.AsyncClient(base_url=self.s.bodhan_base_url, timeout=120.0)
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    # ---- Indic-Transcribe: POST /v1/audio/transcriptions (multipart, <=30s) ----
    async def transcribe(self, audio_bytes: bytes, duration_s: float, lang: str = "en") -> str:
        self.cost.add_transcribe(duration_s)
        if self.s.mock:
            return self._mock_transcript(audio_bytes)

        @retry(retry=retry_if_exception_type(RETRYABLE),
               wait=_retry_after_wait, stop=stop_after_attempt(7))
        async def _call() -> str:
            async with self.sem:
                await self._pace("transcribe")
                files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
                data = {"model": self.s.asr_model, "language": lang}
                r = await self._client.post(
                    "/v1/audio/transcriptions", files=files, data=data,
                    headers={"Authorization": f"Bearer {self.s.transcribe_key}"})
                if r.status_code == 429:
                    self._throttled("transcribe")
                r.raise_for_status()
                return (r.json().get("text") or "").strip()

        return await _call()

    # ---- Indic-Translate: OpenAI-compatible POST /v1/chat/completions ----
    # The model takes a single-user-message prompt naming only the target language
    # (per the model card); the script is selected via a parenthetical suffix.
    _SCRIPT_SUFFIX = {"native": "", "roman": " (Roman)", "codemix": " (code-mixed)"}

    async def translate(self, text: str, target: str, script: str = "native") -> tuple[str, int]:
        """Return (translation, output_tokens). Never called to write English."""
        if self.s.mock:
            toks = max(1, len(text) // 4)
            self.cost.add_translate(toks)
            return self._mock_translate(text, target, script), toks

        suffix = self._SCRIPT_SUFFIX.get(script, "")
        prompt = f"Translate the following text into {target}{suffix}:\n\n{text}"

        @retry(retry=retry_if_exception_type(RETRYABLE),
               wait=_retry_after_wait, stop=stop_after_attempt(7))
        async def _call() -> tuple[str, int]:
            async with self.sem:
                await self._pace("translate")
                payload = {"model": "indic-translate",
                           "messages": [{"role": "user", "content": prompt}]}
                r = await self._client.post(
                    "/v1/chat/completions", json=payload,
                    headers={"Authorization": f"Bearer {self.s.translate_key}"})
                if r.status_code == 429:
                    self._throttled("translate")
                r.raise_for_status()
                body = r.json()
                out = (body["choices"][0]["message"].get("content") or "").strip()
                toks = int(body.get("usage", {}).get("completion_tokens", 0)) or max(1, len(out) // 4)
                self.cost.add_translate(toks)
                return out, toks

        return await _call()

    # ---- Indic-Speak: POST /v1/audio/speech (json -> wav bytes) ----
    async def speak(self, text: str, voice: str, lang: str, style: str = "") -> bytes:
        self.cost.add_speak(len(text))
        if self.s.mock:
            # ~14 chars/sec speaking rate -> plausible, drift-inducing durations.
            return _mock_tone(max(0.3, len(text) / 14.0))

        import json as _json

        # 'instructions' is required and carries the language; 'style' is optional and
        # must be a valid style (an unknown style 500s), so we include it only when set.
        instructions: dict = {"lang": lang}
        if style:
            instructions["style"] = style

        @retry(retry=retry_if_exception_type(RETRYABLE),
               wait=_retry_after_wait, stop=stop_after_attempt(7))
        async def _call() -> bytes:
            async with self.sem:
                await self._pace("speak")
                payload = {
                    "model": "indic-speak",
                    "input": text,
                    "voice": voice,
                    "instructions": _json.dumps(instructions),
                }
                r = await self._client.post(
                    "/v1/audio/speech", json=payload,
                    headers={"Authorization": f"Bearer {self.s.speak_key}"})
                if r.status_code == 429:
                    self._throttled("speak")
                r.raise_for_status()
                return r.content

        return await _call()

    # ---- deterministic mock helpers ----
    @staticmethod
    def _mock_transcript(audio_bytes: bytes) -> str:
        h = int(hashlib.sha1(audio_bytes).hexdigest(), 16)
        pool = [
            "Welcome to today's lesson on triangles and their properties.",
            "A triangle has three sides and three angles that sum to one hundred eighty degrees.",
            "Let us consider an equilateral triangle where all sides are equal.",
            "The area equals one half times base times height.",
            "Remember to label each vertex clearly before you begin.",
        ]
        return pool[h % len(pool)]

    @staticmethod
    def _mock_translate(text: str, target: str, script: str) -> str:
        # ASCII placeholder, clearly not real Indic text, but length-plausible.
        return f"<{target}:{script}> {text}"
