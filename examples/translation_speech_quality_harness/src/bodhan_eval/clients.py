"""Model clients: Indic-Translate, Indic-Speak, Indic-Transcribe (Bodhan) and the LLM judge.

Every call goes through the same path:
cache lookup -> budget reservation -> concurrency slot -> HTTP with retries -> cache store -> ledger.

Retry policy: 429 rate limits and 5xx back off (honouring retry-after); a 429 that says the
account is out of credit is NOT retried, because retries would only burn the rate limit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque

import httpx

from .audio import ensure_wav, wav_duration_s
from .budget import PRICE_SPEAK_PER_CHAR, PRICE_TRANSCRIBE_PER_MIN, PRICE_TRANSLATE_PER_TOKEN, Ledger
from .cache import Cache, bytes_hash
from .config import Settings

log = logging.getLogger(__name__)

TRANSLATE_MODEL = "indic-translate"
SPEAK_MODEL = "indic-speak"
TRANSCRIBE_MODEL = "indic-transcribe"


class ModelError(RuntimeError):
    pass


class CreditExhausted(ModelError):
    pass


class MissingKey(ModelError):
    pass


class LLMUnavailable(ModelError):
    pass


def _backoff(attempt: int, retry_after: str | None = None, throttled: bool = False) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            pass
    if throttled:  # 429 without retry-after: retries count against the limit, so back off harder
        return min(5 * 2 ** attempt, 60) + random.uniform(0, 3)
    return min(2 ** attempt, 30) + random.uniform(0, 0.5)


def max_output_tokens(text: str) -> int:
    """Generous output budget: Indic scripts take ~2-4x the tokens of the English input."""
    return min(8000, max(256, 4 * len(text)))


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", r.json())
        if isinstance(err, dict):
            return f"{err.get('code', '')} {err.get('message', '')}".strip()
        return str(err)[:300]
    except ValueError:
        return r.text[:300]


class BodhanClient:
    def __init__(self, settings: Settings, cache: Cache, ledger: Ledger, http: httpx.AsyncClient | None = None):
        self.s = settings
        self.cache = cache
        self.ledger = ledger
        self.http = http or httpx.AsyncClient(timeout=settings.request_timeout_s)
        self._sem = {k: asyncio.Semaphore(settings.bodhan_concurrency) for k in ("translate", "speak", "transcribe")}
        self._legacy_translate = False

    async def aclose(self) -> None:
        await self.http.aclose()

    def _headers(self, service: str) -> dict:
        key = {"translate": self.s.translate_key, "speak": self.s.speak_key, "transcribe": self.s.transcribe_key}[service]
        if not key:
            raise MissingKey(f"no key for {service}: set BODHAN_{service.upper()}_KEY")
        return {"Authorization": f"Bearer {key}"}

    async def _send(self, service: str, method: str, path: str, **kw) -> httpx.Response:
        url = f"{self.s.bodhan_base_url}{path}"
        last = ""
        for attempt in range(self.s.max_retries + 1):
            async with self._sem[service]:
                try:
                    r = await self.http.request(method, url, headers=self._headers(service), **kw)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last = f"{type(e).__name__}"
                    log.warning("transport error", extra={"event": f"{service}.retry", "attempt": attempt, "error": last})
                    await asyncio.sleep(_backoff(attempt))
                    continue
            if r.status_code == 429:
                text = _error_text(r).lower()
                if any(w in text for w in ("credit", "balance", "budget", "insufficient", "quota")):
                    raise CreditExhausted(f"{service}: {_error_text(r)}")
                last = "429"
                log.info("rate limited", extra={"event": f"{service}.429", "attempt": attempt})
                await asyncio.sleep(_backoff(attempt, r.headers.get("retry-after"), throttled=True))
                continue
            if r.status_code >= 500:
                last = f"{r.status_code} {_error_text(r)}"
                log.warning("server error", extra={"event": f"{service}.retry", "attempt": attempt, "error": last})
                await asyncio.sleep(_backoff(attempt))
                continue
            return r
        raise ModelError(f"{service}: gave up after {self.s.max_retries + 1} attempts ({last})")

    # ------------------------------------------------------------------ translate
    async def translate(self, text: str, target: str, script: str | None = None) -> dict:
        """Returns {"text", "completion_tokens", "truncated"}. Source language is auto-detected."""
        if not text.strip():
            return {"text": "", "completion_tokens": 0, "truncated": False}
        req = {"model": TRANSLATE_MODEL, "text": text, "target": target, "script": script}
        hit = self.cache.get("translate", req)
        if hit is not None:
            self.ledger.record("translate", hit["completion_tokens"] * PRICE_TRANSLATE_PER_TOKEN, hit["completion_tokens"], "output_tokens", cached=True)
            return hit
        self.ledger.reserve(len(text) * PRICE_TRANSLATE_PER_TOKEN)  # rough upper estimate
        out = await (self._translate_legacy(text, target, script) if self._legacy_translate else self._translate_chat(text, target, script))
        self.cache.set("translate", req, out)
        self.ledger.record("translate", out["completion_tokens"] * PRICE_TRANSLATE_PER_TOKEN, out["completion_tokens"], "output_tokens", cached=False)
        return out

    async def _translate_chat(self, text: str, target: str, script: str | None) -> dict:
        # Cap the output: some inputs (long Markdown/LaTeX) can otherwise generate until the request
        # times out. Generation settings like max_tokens are rejected; max_output_tokens is the documented knob.
        body = {"model": TRANSLATE_MODEL, "messages": [{"role": "user", "content": text}], "target_language": target,
                "max_output_tokens": max_output_tokens(text)}
        if script:
            body["script"] = script
        r = await self._send("translate", "POST", "/v1/chat/completions", json=body)
        if r.status_code == 404:  # older deployments expose the plain-JSON route instead
            self._legacy_translate = True
            return await self._translate_legacy(text, target, script)
        if r.status_code != 200:
            raise ModelError(f"translate {r.status_code}: {_error_text(r)}")
        d = r.json()
        choice = d["choices"][0]
        return {
            "text": choice["message"]["content"] or "",
            "completion_tokens": int((d.get("usage") or {}).get("completion_tokens", 0)),
            "truncated": choice.get("finish_reason") == "length",
        }

    async def _translate_legacy(self, text: str, target: str, script: str | None) -> dict:
        body = {"text": text, "target_language": target}
        if script:
            body["script"] = script
        r = await self._send("translate", "POST", "/translate", json=body)
        if r.status_code != 200:
            raise ModelError(f"translate {r.status_code}: {_error_text(r)}")
        d = r.json()
        return {"text": d.get("translation", ""), "completion_tokens": int((d.get("usage") or {}).get("completion_tokens", 0)), "truncated": bool(d.get("truncated"))}

    # ------------------------------------------------------------------ speak
    async def speak(self, text: str, lang: str, voice: str, style: str | None = None) -> bytes:
        if not text.strip():
            raise ValueError("empty text for speech")
        instructions = {"lang": lang} | ({"style": style} if style else {})
        req = {"model": SPEAK_MODEL, "input": text, "voice": voice, "instructions": instructions}
        hit = self.cache.get("speak", req)
        cost = len(text) * PRICE_SPEAK_PER_CHAR
        if hit is not None:
            self.ledger.record("speak", cost, len(text), "input_chars", cached=True)
            return self.cache.blob_path(hit["blob"]).read_bytes()
        self.ledger.reserve(cost)
        body = {"model": SPEAK_MODEL, "input": text, "voice": voice, "instructions": json.dumps(instructions, ensure_ascii=False)}
        r = await self._send("speak", "POST", "/v1/audio/speech", json=body)
        if r.status_code != 200:
            raise ModelError(f"speak {r.status_code}: {_error_text(r)}")
        wav = ensure_wav(r.content)  # the API labels WAV as audio/mpeg; trust the bytes, not the header
        self.cache.set("speak", req, {"blob": self.cache.put_blob(wav)})
        self.ledger.record("speak", cost, len(text), "input_chars", cached=False)
        return wav

    # ------------------------------------------------------------------ transcribe
    async def transcribe(self, wav: bytes, lang: str) -> str:
        req = {"model": TRANSCRIBE_MODEL, "audio_sha256": bytes_hash(wav), "language": lang}
        minutes = wav_duration_s(wav) / 60.0
        cost = minutes * PRICE_TRANSCRIBE_PER_MIN
        hit = self.cache.get("transcribe", req)
        if hit is not None:
            self.ledger.record("transcribe", cost, minutes, "audio_minutes", cached=True)
            return hit["text"]
        self.ledger.reserve(cost)
        r = await self._send("transcribe", "POST", "/v1/audio/transcriptions",
                             files={"file": ("audio.wav", wav, "audio/wav")}, data={"model": TRANSCRIBE_MODEL, "language": lang})
        if r.status_code != 200:
            raise ModelError(f"transcribe {r.status_code}: {_error_text(r)}")
        text = r.json().get("text", "") or ""
        self.cache.set("transcribe", req, {"text": text})
        self.ledger.record("transcribe", cost, minutes, "audio_minutes", cached=False)
        return text


class TokenRateLimiter:
    """Client-side tokens-per-minute limiter, so free-tier LLM endpoints are not hammered into 429s."""

    def __init__(self, tpm: int):
        self.tpm = tpm
        self.window: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int) -> None:
        if self.tpm <= 0:
            return
        tokens = min(tokens, self.tpm)
        async with self._lock:
            while True:
                now = time.monotonic()
                while self.window and now - self.window[0][0] > 60:
                    self.window.popleft()
                used = sum(t for _, t in self.window)
                if used + tokens <= self.tpm:
                    self.window.append((now, tokens))
                    return
                await asyncio.sleep(max(0.2, 60 - (now - self.window[0][0]) + 0.1))


class LLMClient:
    """OpenAI-compatible chat client (any provider), configured only by LLM_* variables."""

    def __init__(self, settings: Settings, cache: Cache, ledger: Ledger):
        from openai import AsyncOpenAI

        self.s = settings
        self.cache = cache
        self.ledger = ledger
        self.model = settings.llm_model
        # The SDK honours retry-after itself; keep retries modest so an exhausted daily quota is noticed quickly.
        self._client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, max_retries=min(settings.max_retries, 4),
                                   timeout=settings.request_timeout_s)
        self._sem = asyncio.Semaphore(settings.llm_concurrency)
        self._limiter = TokenRateLimiter(settings.llm_tokens_per_minute)
        self.cache_only = False  # set when the provider quota is exhausted: cached answers still count

    async def chat(self, messages: list[dict], *, max_tokens: int = 1500, sample: int = 0) -> str:
        """Temperature 0. `sample` > 0 asks for an independent re-run (used to measure consistency)."""
        req = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "sample": sample, "extra": self.s.llm_extra_body}
        hit = self.cache.get("llm", req)
        if hit is not None:
            self.ledger.record_llm(hit["prompt_tokens"], hit["completion_tokens"], cached=True)
            return hit["text"]
        if self.cache_only:
            raise LLMUnavailable("LLM quota exhausted and this request is not cached")
        # rough token estimate (Indic text is token-heavy); typical replies are far below max_tokens
        est = sum(len(m["content"]) for m in messages) // 3 + min(max_tokens, 600)
        await self._limiter.acquire(est)
        async with self._sem:
            kwargs = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
            if self.s.llm_extra_body:
                kwargs["extra_body"] = self.s.llm_extra_body
            resp = await self._client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        out = {"text": text, "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0, "completion_tokens": getattr(usage, "completion_tokens", 0) or 0}
        self.cache.set("llm", req, out)
        self.ledger.record_llm(out["prompt_tokens"], out["completion_tokens"], cached=False)
        return text

    async def aclose(self) -> None:
        await self._client.close()
