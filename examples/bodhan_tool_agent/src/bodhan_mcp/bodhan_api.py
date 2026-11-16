"""Async client for the four hosted Bodhan models.

Responsibilities that every tool relies on:

* one key per model (``BODHAN_*_KEY``, falling back to ``BODHAN_API_KEY``)
* a concurrency limit per model (``BODHAN_MAX_CONCURRENCY``)
* retries with exponential backoff and jitter on timeouts, 429 and 5xx, honouring ``Retry-After``
* a content-addressed disk cache plus in-flight de-duplication, so the same input is never sent
  to the same model twice (within a run or across reruns)
* exact cost accounting from the response (audio seconds, output tokens, characters, images)
* Bodhan's error envelope ``{"error": {"message", "code", "request_id"}}`` mapped to ``ToolFailure``
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import random
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from . import costs
from .config import KEY_ENV, Settings
from .errors import (
    AUTH_FAILED,
    INVALID_INPUT,
    MISSING_API_KEY,
    RATE_LIMITED,
    UNSUPPORTED_LANGUAGE,
    UPSTREAM_ERROR,
    UPSTREAM_TIMEOUT,
    ToolFailure,
)

log = logging.getLogger("bodhan_mcp.api")

TRANSCRIBE = "indic-transcribe"
TRANSLATE = "indic-translate"
SPEAK = "indic-speak"
OCR = "indic-ocr"


class ResponseCache:
    """Content-addressed cache on disk: ``<dir>/<model>/<sha256>.json|.bin``."""

    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled

    @staticmethod
    def key(model: str, params: dict[str, Any], blob: bytes | None = None) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update(json.dumps(params, sort_keys=True, ensure_ascii=False).encode())
        if blob is not None:
            h.update(hashlib.sha256(blob).digest())
        return h.hexdigest()

    def _path(self, model: str, key: str, suffix: str) -> Path:
        return self.root / model / key[:2] / f"{key}{suffix}"

    def get(self, model: str, key: str) -> Any | None:
        if not self.enabled:
            return None
        p = self._path(model, key, ".json")
        if not p.exists():
            return None
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if entry.get("blob"):
            b = self._path(model, key, ".bin")
            if not b.exists():
                return None
            entry["value"] = b.read_bytes()
        return entry

    def put(self, model: str, key: str, value: Any, meta: dict[str, Any]) -> None:
        if not self.enabled:
            return
        p = self._path(model, key, ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {"meta": meta, "created": time.time()}
        if isinstance(value, bytes):
            b = self._path(model, key, ".bin")
            tmp = b.with_suffix(".bin.tmp")
            tmp.write_bytes(value)
            os.replace(tmp, b)
            entry["blob"] = True
        else:
            entry["value"] = value
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate() or 1)
    except (wave.Error, EOFError):
        return 0.0


class BodhanAPI:
    def __init__(self, settings: Settings, ledger: costs.Ledger | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.ledger = ledger or costs.Ledger()
        self.cache = ResponseCache(settings.cache_dir, settings.use_cache)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._key_ok: set[str] = set()

    # -- plumbing ---------------------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.settings.base_url, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _sem(self, model: str) -> asyncio.Semaphore:
        if model not in self._sems:
            self._sems[model] = asyncio.Semaphore(self.settings.max_concurrency)
        return self._sems[model]

    def has_key(self, model: str) -> bool:
        return bool(self.settings.keys.get(model))

    def require_key(self, model: str) -> str:
        key = self.settings.keys.get(model)
        if not key:
            env = KEY_ENV[model]
            raise ToolFailure(
                MISSING_API_KEY,
                f"No API key for {model}: set {env} (or BODHAN_API_KEY) in the server's environment.",
                hint="Keys are issued per model at https://console.bodhan.ai. Restart the server after setting it.",
                details={"model": model, "env_var": env},
            )
        return key

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(self.settings.backoff_cap_s, max(0.0, float(retry_after)))
            except ValueError:
                pass
        base = self.settings.backoff_base_s * (2 ** attempt)
        return min(self.settings.backoff_cap_s, base) * (0.5 + random.random() / 2)

    @staticmethod
    def _envelope(resp: httpx.Response) -> tuple[str | None, str, str | None]:
        try:
            body = resp.json()
        except ValueError:
            return None, resp.text[:300] or resp.reason_phrase, None
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            return err.get("code"), str(err.get("message") or ""), err.get("request_id")
        if isinstance(body, dict) and "detail" in body:
            return None, json.dumps(body["detail"], ensure_ascii=False)[:500], None
        return None, resp.text[:300], None

    async def _send(self, model: str, method: str, path: str, *, no_retry_status: tuple[int, ...] = (), **kw: Any) -> tuple[httpx.Response, int]:
        """Send with retries. Returns the successful response and the number of retries used."""
        key = self.require_key(model)
        timeout = self.settings.timeouts_s.get(model, 60.0)
        attempt = 0
        auth_retry_used = False
        last_exc: Exception | None = None
        while True:
            try:
                async with self._sem(model):
                    resp = await self._http().request(
                        method, path, headers={"Authorization": f"Bearer {key}"}, timeout=timeout, **kw
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                self.ledger.record(model, failed=True)
                if attempt >= self.settings.max_retries:
                    code = UPSTREAM_TIMEOUT if isinstance(exc, httpx.TimeoutException) else UPSTREAM_ERROR
                    raise ToolFailure(
                        code,
                        f"{model} did not respond after {attempt + 1} attempts ({type(exc).__name__}).",
                        hint="The service may be slow or down; retry later or with smaller input.",
                        retryable=True,
                        details={"model": model, "attempts": attempt + 1},
                    ) from exc
                await asyncio.sleep(self._backoff(attempt, None))
                attempt += 1
                continue

            if resp.is_success:
                self._key_ok.add(model)
                return resp, attempt

            self.ledger.record(model, failed=True)
            code, message, request_id = self._envelope(resp)
            status = resp.status_code
            details = {"model": model, "status": status, "api_code": code, "request_id": request_id or resp.headers.get("x-request-id")}

            if status == 401:
                if model in self._key_ok and not auth_retry_used:
                    auth_retry_used = True  # docs: treat auth errors as retryable if the key recently worked
                    await asyncio.sleep(self._backoff(0, None))
                    continue
                raise ToolFailure(AUTH_FAILED, f"{model} rejected the API key: {message}",
                                  hint=f"Check {KEY_ENV[model]}; keys are per model.", details=details)

            budget_hit = status == 429 and any(w in message.lower() for w in ("budget", "quota", "credit", "balance"))
            retryable = (status == 429 and not budget_hit) or status >= 500
            if retryable and status not in no_retry_status and attempt < self.settings.max_retries:
                await asyncio.sleep(self._backoff(attempt, resp.headers.get("retry-after")))
                attempt += 1
                continue

            if status == 429:
                raise ToolFailure(
                    RATE_LIMITED,
                    f"{model} rate limit or account budget reached: {message}",
                    hint="Account credit exhausted; top up in the console." if budget_hit else "Wait a minute and retry.",
                    retryable=not budget_hit,
                    details=details,
                )
            if code == "unsupported_language":
                raise ToolFailure(UNSUPPORTED_LANGUAGE, f"{model}: {message}", details=details)
            if 400 <= status < 500:
                raise ToolFailure(INVALID_INPUT, f"{model} rejected the request ({status}): {message}",
                                  hint="Fix the input described in the message; resending it unchanged will fail again.",
                                  details=details)
            raise ToolFailure(UPSTREAM_ERROR, f"{model} failed ({status}): {message}", retryable=True,
                              details={**details, "attempts": attempt + 1, "last_exception": repr(last_exc) if last_exc else None})

    async def _cached(self, model: str, ckey: str, meter: costs.CallMeter | None, produce) -> Any:
        """Serve from cache, join an identical in-flight request, or call ``produce``."""
        hit = self.cache.get(model, ckey)
        if hit is not None:
            self.ledger.record(model, cached=True)
            if meter:
                meter.add(model, 0.0, cached=True)
            return hit["value"]
        if ckey in self._inflight:
            value = await asyncio.shield(self._inflight[ckey])
            self.ledger.record(model, cached=True)
            if meter:
                meter.add(model, 0.0, cached=True)
            return value
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[ckey] = fut
        try:
            value, units, cost, retries, meta = await produce()
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                fut.cancel()
            else:
                fut.set_exception(exc)
                fut.exception()  # mark retrieved so an unawaited future does not warn
            raise
        finally:
            self._inflight.pop(ckey, None)
        fut.set_result(value)
        self.ledger.record(model, units=units, cost=cost)
        if meter:
            meter.add(model, cost, cached=False, retries=retries)
        self.cache.put(model, ckey, value, {**meta, "units": units, "cost_inr": cost})
        return value

    # -- models ------------------------------------------------------------------------------

    async def transcribe(self, wav: bytes, language: str | None, meter: costs.CallMeter | None = None) -> str:
        params = {"language": language}
        ckey = self.cache.key(TRANSCRIBE, params, wav)
        seconds = wav_seconds(wav)

        async def produce():
            data = {"model": TRANSCRIBE}
            if language:
                data["language"] = language
            resp, retries = await self._send(TRANSCRIBE, "POST", "/v1/audio/transcriptions",
                                             files={"file": ("audio.wav", wav, "audio/wav")}, data=data)
            text = str(resp.json().get("text", ""))
            return text, seconds, costs.transcribe_cost(seconds), retries, {"language": language, "seconds": seconds}

        return await self._cached(TRANSCRIBE, ckey, meter, produce)

    async def translate(self, text: str, target: str, script: str | None = None, max_output_tokens: int | None = None,
                        meter: costs.CallMeter | None = None) -> dict:
        body: dict[str, Any] = {"text": text, "target_language": target}
        if script and script != "native":
            body["script"] = script
        if max_output_tokens:
            body["max_output_tokens"] = max_output_tokens
        ckey = self.cache.key(TRANSLATE, body)

        async def produce():
            resp, retries = await self._send(TRANSLATE, "POST", "/translate", json=body)
            data = resp.json()
            if "translation" not in data:
                raise ToolFailure(UPSTREAM_ERROR, "indic-translate returned no translation field.", retryable=True,
                                  details={"response": str(data)[:300]})
            usage = data.get("usage") or {}
            tokens = int(usage.get("completion_tokens") or 0)
            if not tokens:  # no usage block: estimate conservatively
                tokens = costs.estimate_translate_tokens(len(data["translation"])) // 2
            value = {
                "translation": data["translation"],
                "truncated": bool(data.get("truncated", False)),
                "target_language": data.get("target_language", target),
                "script": data.get("script"),
                "output_tokens": tokens,
            }
            return value, tokens, costs.translate_cost(tokens), retries, {"target": target}

        return await self._cached(TRANSLATE, ckey, meter, produce)

    async def speak(self, text: str, voice: str, language: str, style: str | None = None,
                    meter: costs.CallMeter | None = None) -> bytes:
        instructions: dict[str, str] = {"lang": language}
        if style:
            instructions["style"] = style
        body = {"model": SPEAK, "input": text, "voice": voice, "instructions": json.dumps(instructions, ensure_ascii=False)}
        ckey = self.cache.key(SPEAK, body)

        async def produce():
            resp, retries = await self._send(SPEAK, "POST", "/v1/audio/speech", json=body)
            audio = resp.content
            if wav_seconds(audio) <= 0:
                raise ToolFailure(UPSTREAM_ERROR, "indic-speak returned audio that is not a readable WAV.", retryable=True,
                                  details={"content_type": resp.headers.get("content-type"), "bytes": len(audio)})
            chars = len(text)
            return audio, chars, costs.speak_cost(chars), retries, {"voice": voice, "lang": language}

        return await self._cached(SPEAK, ckey, meter, produce)

    async def ocr(self, image: bytes, mime: str, table_format: str = "markdown", max_tokens: int | None = None,
                  meter: costs.CallMeter | None = None) -> dict:
        params: dict[str, Any] = {"table_format": table_format, "max_tokens": max_tokens}
        ckey = self.cache.key(OCR, params, image)

        async def produce():
            b64 = base64.b64encode(image).decode()
            body: dict[str, Any] = {
                "model": OCR,
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
                "table_format": table_format,
            }
            if max_tokens:
                body["max_tokens"] = max_tokens
            # A 502 usually means a block exceeded max_tokens: the tool retries with a larger limit instead.
            resp, retries = await self._send(OCR, "POST", "/v1/chat/completions", json=body, no_retry_status=(502,))
            data = resp.json()
            try:
                content = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise ToolFailure(UPSTREAM_ERROR, "indic-ocr returned an unexpected response shape.", retryable=True,
                                  details={"response": str(data)[:300]}) from exc
            value = {"markdown": content, "blocks": data.get("blocks") or []}
            return value, 1, costs.ocr_cost(1), retries, {"table_format": table_format}

        return await self._cached(OCR, ckey, meter, produce)
