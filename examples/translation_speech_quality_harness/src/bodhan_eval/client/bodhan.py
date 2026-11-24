import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..budget import Budget
from ..cache import Cache, make_key
from .errors import BodhanAPIError, BudgetExhausted

BASE_URL = "https://api.bodhan.ai"


@dataclass(frozen=True)
class TranslateResult:
    text: str
    truncated: bool
    usage: dict


class BodhanClient:
    def __init__(
        self,
        translate_key: str,
        speak_key: str,
        transcribe_key: str,
        cache: Cache,
        budget: Budget,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], object] | None = None,
    ):
        self.translate_key = translate_key
        self.speak_key = speak_key
        self.transcribe_key = transcribe_key
        self.cache = cache
        self.budget = budget
        self.sleep = sleep or asyncio.sleep
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=60,
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(2)

    async def __aenter__(self) -> "BodhanClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self._http.aclose()

    async def close(self) -> None:
        await self._http.aclose()

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    def _parse_error(self, response: httpx.Response) -> BodhanAPIError:
        try:
            body = response.json()
            error = body.get("error", {})
            message = str(error.get("message", body))
            code = str(error.get("code", "")) or None
            request_id = error.get("request_id")
        except (ValueError, AttributeError):
            message = "malformed API response"
            code = None
            request_id = None
        if response.status_code == 429 and "credit" in message.lower():
            return BudgetExhausted(response.status_code, message, code, request_id)
        return BodhanAPIError(response.status_code, message, code, request_id)

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        *,
        key: str,
        json_body: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        cache_key: str,
        cost: float,
        stage: str,
    ) -> httpx.Response:
        cached = self.cache.get(cache_key)
        if isinstance(cached, dict) and "_http_status" in cached:
            return httpx.Response(cached["_http_status"], json=cached.get("body"), content=cached.get("content"))
        if cached is not None:
            if isinstance(cached, bytes):
                return httpx.Response(200, content=cached)
            return httpx.Response(200, json=cached)
        if not self.budget.can_afford(cost):
            raise BudgetExhausted(429, "budget hard cap reached", "budget_exhausted")

        async with self._semaphore:
            for attempt in range(5):
                response = await self._http.request(
                    method,
                    endpoint,
                    headers=self._headers(key),
                    json=json_body,
                    files=files,
                    data=data,
                )
                parallel = response.headers.get("x-ratelimit-limit-parallel-requests")
                if parallel:
                    try:
                        parallel_count = max(1, int(parallel))
                        if parallel_count != self._semaphore._value:
                            self._semaphore = asyncio.Semaphore(parallel_count)
                    except ValueError:
                        pass

                if response.status_code < 400:
                    self.budget.charge(cost, stage)
                    return response
                error = self._parse_error(response)
                if isinstance(error, BudgetExhausted):
                    raise error
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt == 4:
                        raise error
                    retry_after = response.headers.get("retry-after")
                    delay = float(retry_after) if retry_after else 0.25 * (2 ** attempt)
                    await self.sleep(delay)
                    continue
                raise error
        raise RuntimeError("request semaphore exited unexpectedly")

    async def translate(
        self,
        text: str,
        target_language: str,
        *,
        script: str = "native",
        max_output_tokens: int | None = None,
    ) -> TranslateResult:
        params: dict = {
            "target_language_code": target_language,
            "target_script": script,
        }
        if max_output_tokens is not None:
            params["max_output_tokens"] = max_output_tokens
        payload = text.encode("utf-8")
        endpoint = "/v1/chat/completions"
        cache_key = make_key(endpoint, "indic-translate", params, payload)
        cached = self.cache.get(cache_key)
        if isinstance(cached, dict):
            return TranslateResult(
                text=str(cached.get("text", "")),
                truncated=bool(cached.get("truncated", False)),
                usage=cached.get("usage", {}),
            )

        body = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            **params,
        }
        estimated_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else max(1, len(text.encode("utf-8")) // 4)
        )
        cost = self.budget.estimate_translate(estimated_tokens)
        response = await self._request_with_retry(
            "POST",
            endpoint,
            key=self.translate_key,
            json_body=body,
            cache_key=cache_key,
            cost=cost,
            stage="translate",
        )
        parsed = response.json()
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        text_out = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text_out, str):
            raise ValueError(
                "Bodhan translation response did not contain a string choice message."
            )
        finish_reason = choice.get("finish_reason")
        truncated = finish_reason == "length"
        usage = parsed.get("usage", {}) if isinstance(parsed.get("usage"), dict) else {}
        result = {"text": text_out, "truncated": truncated, "usage": usage}
        self.cache.put(cache_key, result)
        return TranslateResult(text=text_out, truncated=truncated, usage=usage)

    async def transcribe(
        self,
        wav_bytes: bytes,
        language: str,
        *,
        duration_s: float = 30.0,
    ) -> str:
        params = {"language": language}
        cache_key = make_key("/v1/audio/transcriptions", "indic-transcribe", params, wav_bytes)
        cached = self.cache.get(cache_key)
        if isinstance(cached, dict):
            return str(cached.get("text", ""))
        cost = self.budget.estimate_transcribe(duration_s)
        response = await self._request_with_retry(
            "POST",
            "/v1/audio/transcriptions",
            key=self.transcribe_key,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": "indic-transcribe", "language": language},
            cache_key=cache_key,
            cost=cost,
            stage="transcribe",
        )
        parsed = response.json()
        text = str(parsed.get("text", ""))
        self.cache.put(cache_key, {"text": text})
        return text

    async def speak(self, text: str, voice: str, lang: str, style: str | None = None) -> bytes:
        import json

        params = {"voice": voice, "lang": lang, "style": style}
        cache_key = make_key("/v1/audio/speech", "indic-speak", params, text.encode("utf-8"))
        cached = self.cache.get(cache_key)
        if isinstance(cached, bytes):
            return cached
        body = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": json.dumps({"lang": lang}, ensure_ascii=False),
        }
        if style:
            body["style"] = style
        cost = self.budget.estimate_speak(len(text))
        response = await self._request_with_retry(
            "POST",
            "/v1/audio/speech",
            key=self.speak_key,
            json_body=body,
            cache_key=cache_key,
            cost=cost,
            stage="speak",
        )
        audio = response.content
        self.cache.put(cache_key, audio)
        return audio
