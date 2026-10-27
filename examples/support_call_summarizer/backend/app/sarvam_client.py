"""Every call to the Sarvam API lives in this file.

* ``chat()`` -> POST /v1/chat/completions (model ``sarvam-105b`` by default)

The API is OpenAI-compatible. The key goes in the ``api-subscription-key`` header,
and a rejected key answers 403 (not 401). Reasoning tokens count against
``max_tokens``, so a cut-off answer (``finish_reason: length``) is reported as
``truncated`` rather than parsed. Tests replace this client with a fake.
"""

from __future__ import annotations

import asyncio
import logging
import random

import httpx

from .llm import ChatResult, LLMError
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)

DEFAULT_MODEL = "sarvam-105b"


class SarvamError(LLMError):
    pass


class SarvamClient:
    provider = "Sarvam"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = "https://api.sarvam.ai",
        reasoning_effort: str | None = "low",
        max_tokens: int = 4096,
        timeout: float = 120.0,
        max_retries: int = 4,
        rpm: int = 0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.model = model
        self._key = api_key
        # "none" turns reasoning off; the API wants an explicit null for that
        self._reasoning_effort = None if (reasoning_effort or "none").lower() == "none" else reasoning_effort
        self._max_tokens = max_tokens
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._max_retries = max_retries
        self._limiter = RateLimiter(rpm)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(self, messages: list[dict], *, response_format: dict | None = None) -> ChatResult:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": self._max_tokens,
            "reasoning_effort": self._reasoning_effort,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        body = await self._post("/v1/chat/completions", payload)
        try:
            choice = body["choices"][0]
            content = str(choice["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise SarvamError(200, "unexpected_response", "Sarvam returned an unexpected body.") from exc
        return ChatResult(content=content, truncated=choice.get("finish_reason") == "length")

    async def _post(self, path: str, payload: dict) -> dict:
        if not self._key:
            raise SarvamError(None, "missing_api_key", "No Sarvam API key is set.")
        headers = {"api-subscription-key": self._key}
        attempt = 0
        while True:
            error: SarvamError
            retry_after: float | None = None
            await self._limiter.acquire()
            try:
                resp = await self._http.post(path, headers=headers, json=payload)
            except httpx.TimeoutException:
                error = SarvamError(None, "timeout", "Sarvam did not answer in time.")
            except httpx.TransportError as exc:
                error = SarvamError(None, "network_error", f"Could not reach Sarvam ({type(exc).__name__}).")
            else:
                request_id = resp.headers.get("x-request-id")
                if resp.is_success:
                    body = resp.json()
                    usage = body.get("usage") or {}
                    log.info(
                        "sarvam %s -> %s id=%s tokens=%s/%s",
                        path,
                        resp.status_code,
                        request_id or body.get("id"),
                        usage.get("prompt_tokens"),
                        usage.get("completion_tokens"),
                    )
                    return body
                log.info("sarvam %s -> %s id=%s", path, resp.status_code, request_id)
                error = _parse_error(resp, request_id)
                retry_after = _retry_after_seconds(resp)

            retryable = error.status is None or error.status == 429 or error.status >= 500
            if attempt >= self._max_retries or not retryable:
                raise error
            delay = retry_after if retry_after is not None else min(20.0, 2**attempt) + random.uniform(0, 0.5)
            log.warning("sarvam %s %s, retrying in %.1fs (attempt %d)", path, error.code, delay, attempt + 1)
            await asyncio.sleep(delay)
            attempt += 1


def _parse_error(resp: httpx.Response, request_id: str | None) -> SarvamError:
    code, message = f"http_{resp.status_code}", resp.reason_phrase or "Sarvam request failed"
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        err = None
    if isinstance(err, dict):
        code = str(err.get("code") or code)
        message = err.get("message") or message
        request_id = err.get("request_id") or request_id
    return SarvamError(resp.status_code, code, message, request_id)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    try:
        return min(60.0, float(resp.headers["retry-after"]))
    except (KeyError, ValueError):
        return None
