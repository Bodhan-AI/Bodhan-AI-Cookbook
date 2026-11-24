"""Real Bodhan clients over the OpenAI-compatible API at BODHAN_BASE_URL.

Endpoint paths and parameter names come from config/bodhan_api.yaml (OpenAI conventions by
default) so they can be corrected against the live docs without touching this code. Each call
is cached by sha256 of its exact input; a cache hit costs 0. Budget is reserved before any
paid call. Transient failures (429/5xx/timeout) retry with backoff + jitter.
"""

from __future__ import annotations

import io
import json
import re
import wave
from pathlib import Path

from ..cache import Cache, canonical_key
from ..concurrency import ConcurrencyLimiter, RetryableError, with_retries
from ..cost import CostLedger, speak_cost_inr, transcribe_cost_inr, translate_cost_inr
from ..logging_utils import StructuredLogger
from .base import BodhanClients, SpeakResult, TranscribeResult, TranslateResult


def _split_speech_text(text: str, limit: int = 240) -> list[str]:
    """Preserve all text while preferring sentence and word boundaries."""
    chunks = []
    while len(text) > limit:
        prefix = text[:limit + 1]
        boundaries = list(re.finditer(r"[.!?।]\s+|\s+", prefix))
        useful = [m.end() for m in boundaries if m.end() <= limit and m.end() >= limit // 3]
        cut = useful[-1] if useful else limit
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def _join_wav(parts: list[bytes]) -> bytes:
    buffer = io.BytesIO()
    expected = None
    with wave.open(buffer, "wb") as dest:
        for data in parts:
            with wave.open(io.BytesIO(data), "rb") as source:
                spec = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if expected is None:
                    expected = spec
                    dest.setnchannels(spec[0])
                    dest.setsampwidth(spec[1])
                    dest.setframerate(spec[2])
                elif spec != expected:
                    raise ValueError("Speech chunks have incompatible audio formats")
                dest.writeframes(source.readframes(source.getnframes()))
    return buffer.getvalue()


def _openai_client(base_url: str, api_key: str):
    from openai import AsyncOpenAI  # lazy: keep core import-light

    return AsyncOpenAI(base_url=base_url, api_key=api_key)


def _as_retryable(exc: Exception) -> Exception:
    name = type(exc).__name__
    retry_after = getattr(getattr(exc, "response", None), "headers", {}) or {}
    ra = None
    try:
        ra = float(retry_after.get("retry-after")) if retry_after.get("retry-after") else None
    except (TypeError, ValueError):
        ra = None
    if name in ("RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError",
                "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout"):
        return RetryableError(str(exc), retry_after=ra)
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return RetryableError(str(exc), retry_after=ra)
    return exc


class BodhanTranslate:
    def __init__(self, cfg: dict, base_url: str, api_key: str, cache: Cache, ledger: CostLedger,
                 limiter: ConcurrencyLimiter, logger: StructuredLogger):
        self.cfg, self.base_url, self.api_key = cfg, base_url, api_key
        self.cache, self.ledger, self.limiter, self.logger = cache, ledger, limiter, logger
        self.model = cfg["model"]
        self.endpoint = cfg["endpoint"]
        self.rate = cfg["rate_inr_per_10k_output_tokens"]
        self.temperature = cfg.get("temperature", 0)

    async def translate(self, text: str, source_lang: str, target_lang: str) -> TranslateResult:
        max_tokens = max(128, len(text) * 2) if self.ledger.max_inr is not None else None
        params = {"model": self.model, "source": source_lang, "target": target_lang,
                  "temperature": self.temperature, "base_url": self.base_url,
                  "max_tokens": max_tokens}
        key = canonical_key(self.model, self.endpoint, params, text)
        cached = self.cache.get_json(key)
        if cached is not None:
            lc = translate_cost_inr(cached["output_tokens"], self.rate)
            return TranslateResult(text=cached["text"], output_tokens=cached["output_tokens"],
                                   cost_inr=0.0, cached=True, logical_cost_inr=lc)
        # reserve budget with an estimate (output ~ input length in tokens)
        reserved = translate_cost_inr(max_tokens or 0, self.rate)
        self.ledger.reserve(reserved)
        async def call():
            try:
                import httpx

                payload = {"model": self.model, "target_language": target_lang,
                           "messages": [{"role": "user", "content": text}]}
                if max_tokens is not None:
                    payload["max_output_tokens"] = max_tokens
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(self.base_url.rstrip("/") + "/chat/completions",
                                                 json=payload,
                                                 headers={"Authorization": f"Bearer {self.api_key}"})
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:  # noqa: BLE001
                raise _as_retryable(exc) from exc

        try:
            resp = await self.limiter.run(lambda: with_retries(call))
        except BaseException:
            self.ledger.settle(self.model, reserved)
            raise
        choices = resp.get("choices") or []
        out_text = (choices[0].get("message") or {}).get("content", "") if choices else ""
        usage = resp.get("usage")
        out_tokens = (usage or {}).get("completion_tokens") or max(1, len(out_text) // 3)
        cost = translate_cost_inr(out_tokens, self.rate)
        self.ledger.settle(self.model, reserved, cost, estimated=usage is None)
        if not out_text or (choices and choices[0].get("finish_reason") == "length"):
            raise ValueError("Bodhan translation was empty or truncated")
        self.cache.put_json(key, {"text": out_text, "output_tokens": out_tokens})
        self.logger.debug("translate", model=self.model, tokens=out_tokens, cost=round(cost, 5))
        return TranslateResult(text=out_text, output_tokens=out_tokens, cost_inr=cost,
                               cached=False, logical_cost_inr=cost)


class BodhanSpeak:
    def __init__(self, cfg: dict, base_url: str, api_key: str, cache: Cache, ledger: CostLedger,
                 limiter: ConcurrencyLimiter, logger: StructuredLogger):
        self.cfg, self.base_url, self.api_key = cfg, base_url, api_key
        self.cache, self.ledger, self.limiter, self.logger = cache, ledger, limiter, logger
        self.model = cfg["model"]
        self.endpoint = cfg["endpoint"]
        self.rate = cfg["rate_inr_per_10k_input_chars"]
        self.response_format = cfg.get("response_format", "wav")

    async def speak(self, text: str, lang: str, voice: str | None = None) -> SpeakResult:
        voice = voice or self.cfg.get("default_voice") or "Kavya"
        chunks = _split_speech_text(text)
        if len(chunks) > 1:
            results = [await self.speak(chunk, lang, voice) for chunk in chunks]
            audio = _join_wav([r.audio_bytes or Path(r.audio_path).read_bytes() for r in results])
            key = canonical_key(self.model, "chunked_speech_v1",
                                {"voice": voice, "lang": lang, "base_url": self.base_url}, text)
            path = self.cache.put_bytes(key, audio, ".wav")
            return SpeakResult(str(path), len(text), sum(r.cost_inr for r in results),
                               all(r.cached for r in results), audio,
                               sum(r.logical_cost_inr for r in results))
        params = {"model": self.model, "voice": voice, "lang": lang,
                  "format": self.response_format, "base_url": self.base_url}
        key = canonical_key(self.model, self.endpoint, params, text)
        suffix = f".{self.response_format}"
        cached = self.cache.get_bytes(key, suffix)
        if cached is not None:
            lc = speak_cost_inr(len(text), self.rate)
            return SpeakResult(audio_path=str(self.cache.path_for(key, suffix)),
                               input_chars=len(text), cost_inr=0.0, cached=True,
                               audio_bytes=cached, logical_cost_inr=lc)
        cost = speak_cost_inr(len(text), self.rate)
        self.ledger.reserve(cost)

        async def call():
            try:
                import httpx

                async with httpx.AsyncClient(timeout=120) as client:
                    response = await client.post(
                        self.base_url.rstrip("/") + "/audio/speech",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "voice": voice, "input": text,
                              "instructions": json.dumps({"lang": lang})})
                    response.raise_for_status()
                    return response.content
            except Exception as exc:  # noqa: BLE001
                raise _as_retryable(exc) from exc

        try:
            audio = await self.limiter.run(lambda: with_retries(call))
        except BaseException:
            self.ledger.settle(self.model, cost)
            raise
        self.ledger.settle(self.model, cost, cost)
        path = self.cache.put_bytes(key, audio, suffix)
        self.logger.debug("speak", model=self.model, chars=len(text), cost=round(cost, 5))
        return SpeakResult(audio_path=str(path), input_chars=len(text), cost_inr=cost,
                           cached=False, audio_bytes=audio, logical_cost_inr=cost)


class BodhanTranscribe:
    def __init__(self, cfg: dict, base_url: str, api_key: str, cache: Cache, ledger: CostLedger,
                 limiter: ConcurrencyLimiter, logger: StructuredLogger):
        self.cfg, self.base_url, self.api_key = cfg, base_url, api_key
        self.cache, self.ledger, self.limiter, self.logger = cache, ledger, limiter, logger
        self.model = cfg["model"]
        self.endpoint = cfg["endpoint"]
        self.rate = cfg["rate_inr_per_audio_minute"]
        self.response_format = cfg.get("response_format", "json")

    async def transcribe(self, audio_path: str, lang: str) -> TranscribeResult:
        data = Path(audio_path).read_bytes()
        seconds = _audio_seconds(audio_path)
        if seconds > self.cfg.get("max_audio_seconds", 30):
            import soundfile as sf

            audio, sr = sf.read(audio_path, dtype="float32", always_2d=True)
            step = int(sr * min(28, self.cfg.get("max_audio_seconds", 30)))
            if step <= 0:
                raise ValueError("Invalid transcription chunk duration")
            results = []
            for start in range(0, len(audio), step):
                buffer = io.BytesIO()
                sf.write(buffer, audio[start:start + step], sr, format="WAV", subtype="PCM_16")
                chunk_data = buffer.getvalue()
                key = canonical_key(self.model, "audio_chunk_v1", {"lang": lang}, chunk_data)
                chunk_path = self.cache.put_bytes(key, chunk_data, ".wav")
                results.append(await self.transcribe(str(chunk_path), lang))
            return TranscribeResult(" ".join(r.text.strip() for r in results if r.text.strip()),
                                    seconds, sum(r.cost_inr for r in results),
                                    all(r.cached for r in results),
                                    sum(r.logical_cost_inr for r in results))
        params = {"model": self.model, "lang": lang, "format": self.response_format,
                  "base_url": self.base_url}
        key = canonical_key(self.model, self.endpoint, params, data)
        cached = self.cache.get_json(key)
        if cached is not None:
            lc = transcribe_cost_inr(seconds, self.rate)
            return TranscribeResult(text=cached["text"], audio_seconds=seconds, cost_inr=0.0,
                                    cached=True, logical_cost_inr=lc)
        cost = transcribe_cost_inr(seconds, self.rate)
        self.ledger.reserve(cost)

        async def call():
            try:
                client = _openai_client(self.base_url, self.api_key)
                with open(audio_path, "rb") as fh:
                    return await client.audio.transcriptions.create(
                        model=self.model, file=fh, language=lang)
            except Exception as exc:  # noqa: BLE001
                raise _as_retryable(exc) from exc

        try:
            resp = await self.limiter.run(lambda: with_retries(call))
        except BaseException:
            self.ledger.settle(self.model, cost)
            raise
        text = getattr(resp, "text", None) or (resp.get("text") if isinstance(resp, dict) else "")
        self.ledger.settle(self.model, cost, cost)
        self.cache.put_json(key, {"text": text})
        self.logger.debug("transcribe", model=self.model, seconds=round(seconds, 2),
                          cost=round(cost, 5))
        return TranscribeResult(text=text, audio_seconds=seconds, cost_inr=cost, cached=False,
                                logical_cost_inr=cost)



def _audio_seconds(path: str) -> float:
    try:
        import soundfile as sf

        info = sf.info(path)
        return info.frames / float(info.samplerate)
    except Exception:  # noqa: BLE001
        # fall back to a rough size-based estimate (never fatal)
        size = Path(path).stat().st_size
        return max(0.1, size / (24000 * 2))


def build_bodhan_clients(api_cfg: dict, cache: Cache, ledger: CostLedger,
                         limiter: ConcurrencyLimiter, logger: StructuredLogger,
                         env: dict[str, str]) -> BodhanClients:
    import os

    base_url = env.get(api_cfg["base_url_env"]) or api_cfg["default_base_url"]

    def key_for(section: str) -> str:
        name = api_cfg[section]["key_env"]
        val = env.get(name) or os.environ.get(name)
        if not val:
            raise RuntimeError(f"missing env {name} for Bodhan {section}")
        return val

    return BodhanClients(
        translate=BodhanTranslate(api_cfg["translate"], base_url, key_for("translate"),
                                  cache, ledger, limiter, logger),
        speak=BodhanSpeak(api_cfg["speak"], base_url, key_for("speak"), cache, ledger,
                          limiter, logger),
        transcribe=BodhanTranscribe(api_cfg["transcribe"], base_url, key_for("transcribe"),
                                    cache, ledger, limiter, logger),
    )
