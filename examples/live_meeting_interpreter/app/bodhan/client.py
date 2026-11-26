"""Async client for the three Bodhan endpoints this app uses.

Contracts (https://console.bodhan.ai/api-docs/):
  ASR  POST {base}/v1/audio/transcriptions  multipart: file, model=indic-transcribe, language
       -> {"text": ...}. Max 30 s audio. Billed per second of audio.
  MT   POST {base}/v1/chat/completions  json: model=indic-translate, target_language, script?,
       messages=[one user turn with the text] -> choices[0].message.content, finish_reason.
       (The docs' POST /translate is 404 on the live API.) No source-language field.
       temperature/stream/max_tokens etc are REJECTED.
  TTS  POST {base}/v1/audio/speech  json: model=indic-speak, input, voice,
       instructions=JSON string {"lang", "style"?} -> audio/wav PCM16 24 kHz mono.
Errors: {"error": {"message", "code", "request_id"}}; TTS 422 may use FastAPI "detail".
Keys are per model.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.bodhan.ratelimit import RateLimiter
from app.config import Settings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
AUTH_GRACE_S = 300.0  # docs: treat 401 as retryable if the same key recently succeeded


class BodhanError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 request_id: Optional[str] = None, retryable: bool = False) -> None:
        super().__init__(f"[{status} {code}] {message}" + (f" (request {request_id})" if request_id else ""))
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retryable = retryable


@dataclass
class _Endpoint:
    name: str
    key: str
    limiter: RateLimiter
    last_success: float = float("-inf")
    checked_headers: bool = field(default=False)


def _parse_error(resp: httpx.Response) -> BodhanError:
    code, message, rid = str(resp.status_code), resp.reason_phrase, resp.headers.get("x-request-id")
    try:
        body: Any = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or code)
            message = str(err.get("message") or message)
            rid = err.get("request_id") or rid
        elif "detail" in body:
            message = json.dumps(body["detail"], ensure_ascii=False)[:500]
    return BodhanError(resp.status_code, code, message, rid, resp.status_code in RETRYABLE_STATUS)


def _retry_after(resp: httpx.Response) -> Optional[float]:
    value = resp.headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None


class BodhanClient:
    def __init__(self, settings: Settings, http: Optional[httpx.AsyncClient] = None) -> None:
        self._base = settings.base_url.rstrip("/")
        self._limits = settings.limits
        shared = (RateLimiter(settings.limits.rpm, settings.limits.concurrency)
                  if settings.shared_limiter else None)

        def limiter(model: str) -> RateLimiter:
            return shared or RateLimiter(settings.limits.rpm_for(model), settings.limits.concurrency)

        self._ep = {
            "transcribe": _Endpoint("transcribe", settings.transcribe_key, limiter("transcribe")),
            "translate": _Endpoint("translate", settings.translate_key, limiter("translate")),
            "speak": _Endpoint("speak", settings.speak_key, limiter("speak")),
        }
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    def headroom(self, model: str) -> int:
        """Requests to `model` ("transcribe" | "translate" | "speak") that could start now."""
        return self._ep[model].limiter.headroom()

    # ------------------------------------------------------------------ core

    def _observe_headers(self, ep: _Endpoint, resp: httpx.Response) -> None:
        if ep.checked_headers:
            return
        ep.checked_headers = True
        # docs name them x-ratelimit-limit-*; the live gateway sends x-ratelimit-api_key-limit-*
        rpm = (resp.headers.get("x-ratelimit-limit-requests")
               or resp.headers.get("x-ratelimit-api_key-limit-requests"))
        par = (resp.headers.get("x-ratelimit-limit-parallel-requests")
               or resp.headers.get("x-ratelimit-api_key-limit-max_parallel_requests"))
        log.info("Bodhan %s published limits: requests=%s parallel=%s", ep.name, rpm, par)
        try:
            if rpm and int(rpm) < self._limits.rpm_for(ep.name):
                log.warning("Configured %d rpm exceeds the server's %s for %s; set BODHAN_%s_RPM",
                            self._limits.rpm_for(ep.name), rpm, ep.name, ep.name.upper())
            if par and int(par) < self._limits.concurrency:
                log.warning("BODHAN_CONCURRENCY=%d exceeds the server's %s for %s",
                            self._limits.concurrency, par, ep.name)
        except ValueError:
            pass

    async def _request(self, ep_name: str, path: str, *, deadline: Optional[float] = None,
                       **kwargs: Any) -> httpx.Response:
        """POST with rate limiting, retries and an optional absolute monotonic deadline.

        A deadline stops retrying once the result would arrive too late to be useful
        (a subtitle 40 s after the sentence is worse than no subtitle).
        """
        ep = self._ep[ep_name]
        url = f"{self._base}{path}"
        attempt = 0
        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise BodhanError(0, "deadline_exceeded", f"{ep_name} result would be stale")
            retry_after: Optional[float] = None
            try:
                async with ep.limiter.slot():
                    resp = await self._http.post(url, headers={"Authorization": f"Bearer {ep.key}"}, **kwargs)
            except httpx.HTTPError as exc:  # timeouts, connection resets, DNS...
                err = BodhanError(0, "network_error", repr(exc), retryable=True)
            else:
                self._observe_headers(ep, resp)
                if resp.status_code < 400:
                    ep.last_success = time.monotonic()
                    return resp
                err = _parse_error(resp)
                retry_after = _retry_after(resp)
                if resp.status_code == 401 and time.monotonic() - ep.last_success < AUTH_GRACE_S:
                    err.retryable = True
                if resp.status_code == 429 and retry_after:
                    ep.limiter.cool_down(retry_after)

            if not err.retryable or attempt >= self._limits.max_retries:
                raise err
            delay = retry_after if retry_after is not None else min(8.0, 0.5 * 2 ** attempt) * random.uniform(0.5, 1.0)
            if deadline is not None and time.monotonic() + delay > deadline:
                raise err
            attempt += 1
            log.warning("%s failed (%s); retry %d/%d in %.1fs", ep_name, err, attempt,
                        self._limits.max_retries, delay)
            await asyncio.sleep(delay)

    # ------------------------------------------------------------ endpoints

    async def transcribe(self, wav: bytes, language: Optional[str] = None,
                         deadline: Optional[float] = None) -> str:
        data = {"model": "indic-transcribe"}
        if language:
            data["language"] = language  # always pass it when known: big accuracy difference
        resp = await self._request("transcribe", "/v1/audio/transcriptions", deadline=deadline,
                                   data=data, files={"file": ("speech.wav", wav, "audio/wav")})
        return str(resp.json().get("text", "")).strip()

    async def translate(self, text: str, target_language: str, script: str = "native",
                        deadline: Optional[float] = None) -> str:
        # The docs' preferred POST /translate returns 404 on the live API (checked 2026-11-25),
        # so use the documented alternative transport: chat completions with model
        # indic-translate. Exactly one user turn, no system turn; target_language and
        # script go top-level, as on /translate. Generation settings are rejected.
        body: dict[str, Any] = {
            "model": "indic-translate",
            "target_language": target_language,
            "messages": [{"role": "user", "content": text}],
        }
        if target_language != "en" and script != "native":
            body["script"] = script  # roman/codemix are invalid for en
        resp = await self._request("translate", "/v1/chat/completions", deadline=deadline, json=body)
        payload = resp.json()
        if "error" in payload:  # docs: branch on presence, not status
            err = payload["error"]
            raise BodhanError(resp.status_code, str(err.get("code")), str(err.get("message")), err.get("request_id"))
        choice = payload["choices"][0]
        if payload.get("truncated") or choice.get("finish_reason") == "length":
            log.warning("Translation truncated (output hit token budget): %r", text[:80])
        return str(choice["message"]["content"]).strip()

    async def speak(self, text: str, lang: str, voice: str, style: Optional[str] = None,
                    deadline: Optional[float] = None) -> bytes:
        instructions: dict[str, str] = {"lang": lang}
        if style:
            instructions["style"] = style
        body = {"model": "indic-speak", "input": text, "voice": voice,
                "instructions": json.dumps(instructions, ensure_ascii=False)}
        resp = await self._request("speak", "/v1/audio/speech", deadline=deadline, json=body)
        return resp.content
