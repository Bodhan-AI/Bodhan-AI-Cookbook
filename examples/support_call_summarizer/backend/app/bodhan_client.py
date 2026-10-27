"""Every call to the Bodhan API lives in this file.

* ``transcribe()`` -> POST /v1/audio/transcriptions (model ``indic-transcribe``)
* ``translate()``  -> POST /v1/chat/completions   (model ``indic-translate``)

The docs prefer ``POST /translate``, but on 2026-10-20 the live gateway answered
404 there while the documented chat-completions transport worked, so this client
uses the chat transport: one user message, plus ``target_language``. Bodhan keys
can be scoped to one model, so each endpoint can have its own key.

Tests replace this client with a fake, and if the Bodhan API changes only this
file needs to change.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

import httpx

from .ratelimit import RateLimiter

log = logging.getLogger(__name__)

TRANSCRIBE_MODEL = "indic-transcribe"
TRANSLATE_MODEL = "indic-translate"


class BodhanError(Exception):
    def __init__(self, status: int | None, code: str, message: str, request_id: str | None = None):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id

    @property
    def is_auth_error(self) -> bool:
        return self.status == 401 or self.code in {"invalid_api_key_error", "missing_api_key"}


@dataclass(frozen=True)
class Translation:
    text: str
    truncated: bool


class BodhanClient:
    def __init__(
        self,
        transcribe_key: str,
        translate_key: str | None = None,
        *,
        base_url: str = "https://api.bodhan.ai",
        timeout: float = 60.0,
        max_retries: int = 4,
        concurrency: int = 4,
        rpm: int = 0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._keys = {"transcribe": transcribe_key, "translate": translate_key or transcribe_key}
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._max_retries = max_retries
        self._slots = asyncio.Semaphore(max(1, concurrency))
        self._limiter = RateLimiter(rpm)
        self._worked: set[str] = set()  # endpoints whose key has succeeded at least once

    async def aclose(self) -> None:
        await self._http.aclose()

    async def transcribe(self, wav: bytes, language: str | None = None) -> str:
        data = {"model": TRANSCRIBE_MODEL}
        if language:
            data["language"] = language
        body = await self._post(
            "transcribe", "/v1/audio/transcriptions", data=data, files={"file": ("piece.wav", wav, "audio/wav")}
        )
        return str(body.get("text") or "").strip()

    async def translate(self, text: str, target_language: str = "en") -> Translation:
        body = await self._post(
            "translate",
            "/v1/chat/completions",
            json={
                "model": TRANSLATE_MODEL,
                "target_language": target_language,
                "messages": [{"role": "user", "content": text}],
            },
        )
        try:
            choice = body["choices"][0]
            content = str(choice["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise BodhanError(200, "unexpected_response", "Bodhan Translation returned an unexpected body.") from exc
        # finish_reason "length" is the chat transport's equivalent of `truncated: true`.
        return Translation(text=content, truncated=choice.get("finish_reason") == "length")

    async def _post(self, endpoint: str, path: str, **kwargs) -> dict:
        key = self._keys[endpoint]
        if not key:
            raise BodhanError(None, "missing_api_key", f"No Bodhan API key is set for {endpoint}.")
        headers = {"Authorization": f"Bearer {key}"}
        attempt = 0
        while True:
            error: BodhanError
            retry_after: float | None = None
            async with self._slots:
                await self._limiter.acquire()
                try:
                    resp = await self._http.post(path, headers=headers, **kwargs)
                except httpx.TimeoutException:
                    error = BodhanError(None, "timeout", "Bodhan did not answer in time.")
                except httpx.TransportError as exc:
                    error = BodhanError(None, "network_error", f"Could not reach Bodhan ({type(exc).__name__}).")
                else:
                    request_id = resp.headers.get("x-request-id")
                    if resp.is_success:
                        body = resp.json()
                        self._worked.add(endpoint)
                        log.info("bodhan %s -> %s id=%s", path, resp.status_code, request_id or body.get("id"))
                        return body
                    log.info("bodhan %s -> %s id=%s", path, resp.status_code, request_id)
                    error = _parse_error(resp, request_id)
                    retry_after = _retry_after_seconds(resp)

            if attempt >= self._max_retries or not self._retryable(endpoint, error, attempt):
                raise error
            delay = retry_after if retry_after is not None else min(20.0, 2**attempt) + random.uniform(0, 0.5)
            log.warning("bodhan %s %s, retrying in %.1fs (attempt %d)", path, error.code, delay, attempt + 1)
            await asyncio.sleep(delay)
            attempt += 1

    def _retryable(self, endpoint: str, error: BodhanError, attempt: int) -> bool:
        if error.status is None or error.status == 429 or error.status >= 500:
            return error.code != "missing_api_key"
        # The docs say to retry an auth error when the same key recently worked.
        return error.is_auth_error and endpoint in self._worked and attempt == 0


def _parse_error(resp: httpx.Response, request_id: str | None) -> BodhanError:
    code, message = f"http_{resp.status_code}", resp.reason_phrase or "Bodhan request failed"
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        err = None
    if isinstance(err, dict):
        code = str(err.get("code") or code)
        message = err.get("message") or message
        request_id = err.get("request_id") or request_id
    elif resp.status_code == 415:
        code, message = "unsupported_media_type", "Bodhan could not read the audio piece."
    return BodhanError(resp.status_code, code, message, request_id)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    try:
        return min(60.0, float(resp.headers["retry-after"]))
    except (KeyError, ValueError):
        return None
