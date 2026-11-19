"""Bodhan model clients: Indic-Translate, Indic-Speak, Indic-Transcribe.

All three go through the hosted API's OpenAI-compatible surface. Path shapes
follow the OpenAI convention (`/chat/completions`, `/audio/speech`,
`/audio/transcriptions`); each is overridable by env var because Bodhan's docs
are the source of truth and this sandbox cannot reach the API to confirm them.

Every call is cached by exact input, retried with backoff, counted against the
rupee budget, and rate-limited by a semaphore.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass, field

import httpx

from . import config
from .cache import Cache


class BudgetExceeded(RuntimeError):
    """Raised instead of making a call that would break --max-cost-inr."""


@dataclass
class CostTracker:
    max_inr: float | None = None
    spent: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)

    def charge(self, model: str, amount: float) -> None:
        if self.max_inr is not None and self.spent + amount > self.max_inr:
            raise BudgetExceeded(
                f"budget of Rs {self.max_inr:.2f} would be exceeded "
                f"(spent Rs {self.spent:.2f}, next call Rs {amount:.4f})"
            )
        self.spent += amount
        self.by_model[model] = self.by_model.get(model, 0.0) + amount
        self.calls[model] = self.calls.get(model, 0) + 1

    def summary(self) -> dict:
        return {
            "total_inr": round(self.spent, 4),
            "by_model_inr": {k: round(v, 4) for k, v in self.by_model.items()},
            "calls": dict(self.calls),
            "budget_inr": self.max_inr,
        }


def estimate_translate_cost(text: str) -> float:
    tokens = max(1, int(len(text) * config.TOKENS_PER_CHAR))
    return tokens / 10_000 * config.COST_TRANSLATE_PER_10K_OUT_TOKENS


def estimate_speak_cost(text: str) -> float:
    return len(text) / 10_000 * config.COST_SPEAK_PER_10K_CHARS


def estimate_transcribe_cost(seconds: float) -> float:
    return (seconds / 60.0) * config.COST_TRANSCRIBE_PER_AUDIO_MINUTE


class BodhanClient:
    """Thin async wrapper over the three hosted models."""

    def __init__(
        self,
        cache: Cache,
        costs: CostTracker,
        *,
        offline: bool = False,
        concurrency: int = 4,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self.cache = cache
        self.costs = costs
        self.offline = offline
        self.max_retries = max_retries
        self.timeout = timeout
        self._sem = asyncio.Semaphore(concurrency)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BodhanClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    # -- plumbing ----------------------------------------------------------
    async def _post(self, url: str, key: str, *, json=None, files=None, data=None,
                    headers=None) -> httpx.Response:
        assert self._client is not None, "use BodhanClient as an async context manager"
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                async with self._sem:
                    r = await self._client.post(
                        url, json=json, files=files, data=data,
                        headers={"Authorization": f"Bearer {key}", **(headers or {})},
                    )
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"retryable {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r
            except Exception as exc:  # noqa: BLE001 - retried below
                last = exc
                if attempt == self.max_retries - 1:
                    break
                delay = min(8.0, 0.5 * 2 ** attempt) + random.uniform(0, 0.3)
                await asyncio.sleep(delay)
        raise RuntimeError(f"request failed after {self.max_retries} attempts: {last}") from last

    # -- translate ---------------------------------------------------------
    async def translate(self, text: str, target_lang: str, source_lang: str = "en") -> str:
        key = Cache.key("translate", config.TRANSLATE_MODEL, source_lang, target_lang, text)
        hit = self.cache.get_json(key)
        if hit is not None:
            return hit["text"]
        if self.offline or not config.have_translate():
            raise RuntimeError("translate unavailable: no BODHAN_TRANSLATE_KEY (or offline)")

        self.costs.charge(config.TRANSLATE_MODEL, estimate_translate_cost(text))
        url = f"{config.BODHAN_BASE_URL}/chat/completions"
        body = {
            "model": config.TRANSLATE_MODEL,
            "messages": [
                {"role": "system", "content":
                    f"Translate the user's text from {source_lang} into {target_lang}. "
                    f"Return only the translation, preserving Markdown and LaTeX exactly."},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
        }
        r = await self._post(url, config.BODHAN_TRANSLATE_KEY, json=body)
        data = r.json()
        out = data["choices"][0]["message"]["content"].strip()
        self.cache.put_json(key, {"text": out, "model": config.TRANSLATE_MODEL})
        return out

    # -- speak -------------------------------------------------------------
    async def speak(self, text: str, lang: str, voice: str | None = None) -> bytes:
        key = Cache.key("speak", config.SPEAK_MODEL, lang, voice, text)
        hit = self.cache.get_bytes(key)
        if hit is not None:
            return hit
        if self.offline or not config.have_speak():
            raise RuntimeError("speak unavailable: no BODHAN_SPEAK_KEY (or offline)")

        self.costs.charge(config.SPEAK_MODEL, estimate_speak_cost(text))
        url = f"{config.BODHAN_BASE_URL}/audio/speech"
        body = {"model": config.SPEAK_MODEL, "input": text, "language": lang,
                "response_format": "wav"}
        if voice:
            body["voice"] = voice
        r = await self._post(url, config.BODHAN_SPEAK_KEY, json=body)
        audio = r.content
        self.cache.put_bytes(key, audio, {"model": config.SPEAK_MODEL, "lang": lang,
                                          "voice": voice, "chars": len(text)})
        return audio

    # -- transcribe --------------------------------------------------------
    async def transcribe(self, audio: bytes, lang: str, filename: str = "a.wav") -> str:
        key = Cache.key("transcribe", config.TRANSCRIBE_MODEL, lang, hashlib.sha256(audio).hexdigest())
        hit = self.cache.get_json(key)
        if hit is not None:
            return hit["text"]
        if self.offline or not config.have_transcribe():
            raise RuntimeError("transcribe unavailable: no BODHAN_TRANSCRIBE_KEY (or offline)")

        self.costs.charge(config.TRANSCRIBE_MODEL, estimate_transcribe_cost(len(audio) / 32000.0))
        url = f"{config.BODHAN_BASE_URL}/audio/transcriptions"
        files = {"file": (filename, audio, "audio/wav")}
        data = {"model": config.TRANSCRIBE_MODEL, "language": lang}
        # model and language belong in the multipart form, not only a header
        r = await self._post(url, config.BODHAN_TRANSCRIBE_KEY, files=files,
                             data=data, headers={"X-Language": lang})
        payload = r.json()
        text = (payload.get("text") or payload.get("transcript") or "").strip()
        self.cache.put_json(key, {"text": text, "model": config.TRANSCRIBE_MODEL})
        return text
