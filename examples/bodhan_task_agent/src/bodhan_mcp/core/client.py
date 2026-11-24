from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar

import httpx

from bodhan_mcp.config import API_KEY_ENV, Settings, api_key, api_key_hint
from bodhan_mcp.exceptions import BodhanToolError, ErrorCode
from bodhan_mcp.schemas.ocr import OcrResult
from bodhan_mcp.schemas.synthesize import SynthesizeResult
from bodhan_mcp.schemas.telemetry import CallRecord
from bodhan_mcp.schemas.transcribe import TranscribeResult
from bodhan_mcp.schemas.translate import TranslateResult, TranslateScript
from bodhan_mcp.utils.audio import sniff_audio_format
from bodhan_mcp.utils.cache import ResultCache, cache_key, content_digest
from bodhan_mcp.utils.documents import sniff_document
from bodhan_mcp.utils.pricing import ocr_cost, speak_cost, transcribe_cost, translate_cost
from bodhan_mcp.utils.rate_limit import RateLimiter
from bodhan_mcp.utils.retry import with_retries
from bodhan_mcp.utils.telemetry import record_call

_REQUEST_ID_HEADERS = ("llm_provider-x-request-id", "x-request-id")
_RATE_LIMIT_CODES = frozenset({"rate_limit_exceeded_error", "rate_limit_exceeded", "rate_limited"})
_CREDIT_CODES = frozenset({"insufficient_credit", "insufficient_balance", "insufficient_funds", "credit_exhausted"})
_CREDIT_WORDS = ("credit", "balance")
T = TypeVar("T")

_VENDOR_REJECTED = "vendor rejected the request"
_AMBIGUOUS_500_MODELS = frozenset({"speak"})
_OCR_TRUNCATED = "truncated"
_OCR_UNDECODABLE = "decode"
_IMAGE_MIME = {"png": "image/png", "jpeg": "image/jpeg"}

_CODE_MAP: dict[str, tuple[ErrorCode, str]] = {
    "payload_too_large": ("input_too_large", "Send a smaller input."),
    "unsupported_language": ("unsupported_language", "Use a language code the model supports."),
    "unsupported_script_for_language": (
        "unsupported_language",
        "This language does not support the requested script; use script='native'.",
    ),
    "invalid_script": ("invalid_input", "script must be 'native', 'roman' or 'codemix'; 'roman' is invalid for 'en'."),
    "invalid_prompt": ("invalid_input", "Send non-empty plain text."),
}


def _error_body(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "", response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or ""), str(error.get("message") or "")[:300]
        if "detail" in body:
            return "", str(body["detail"])[:300]
    return "", str(body)[:300]


def _retry_after_s(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _request_id(response: httpx.Response, body: dict[str, Any] | None = None) -> str | None:
    if body and isinstance(body.get("id"), str):
        return body["id"]
    for header in _REQUEST_ID_HEADERS:
        if header in response.headers:
            return response.headers[header]
    return None


def _is_credit_error(status: int, code: str, message: str) -> bool:
    if code in _CREDIT_CODES or status == 402:
        return True
    if status != 429 or code in _RATE_LIMIT_CODES:
        return False
    lowered = message.lower()
    return any(word in lowered for word in _CREDIT_WORDS)


def _rate_limited(model: str, detail: str, upstream: dict[str, Any], response: httpx.Response) -> BodhanToolError:
    retry_after = _retry_after_s(response)
    if retry_after is not None:
        upstream["retry_after_s"] = retry_after
    return BodhanToolError(
        "rate_limited",
        f"Bodhan {model} rate limit reached: {detail}",
        hint="Wait and retry, or lower BODHAN_MAX_CONCURRENCY.",
        retryable=True,
        details=upstream,
    )


def _status_error(model: str, response: httpx.Response) -> BodhanToolError:
    status = response.status_code
    code, message = _error_body(response)
    detail = message or f"HTTP {status}"
    upstream: dict[str, Any] = {"upstream_status": status}
    if code:
        upstream["upstream_code"] = code
    request_id = _request_id(response)
    if request_id:
        upstream["request_id"] = request_id

    if 300 <= status < 400:
        upstream["redirect_host"] = httpx.URL(response.headers.get("location", "")).host or None
        return BodhanToolError(
            "upstream_error",
            f"Bodhan {model} answered with an unexpected redirect (HTTP {status}); it was not followed.",
            hint="Check BODHAN_BASE_URL.",
            details=upstream,
        )
    if status in (401, 403):
        return BodhanToolError(
            "auth_failed",
            f"Bodhan rejected the {model} API key: {detail}",
            hint=f"Check {api_key_hint(model)}.",
            details=upstream,
        )
    if _is_credit_error(status, code, message):
        return BodhanToolError(
            "insufficient_credit",
            f"Bodhan account is out of credit for {model}: {detail}",
            hint="Top up credit in the Bodhan console.",
            details=upstream,
        )
    if code in _RATE_LIMIT_CODES or status == 429:
        return _rate_limited(model, detail, upstream, response)
    if code in _CODE_MAP:
        error_code, hint = _CODE_MAP[code]
        return BodhanToolError(error_code, f"Bodhan {model}: {detail}", hint=hint, details=upstream)
    if status == 408:
        return BodhanToolError(
            "timeout",
            f"Bodhan {model} timed out handling the request.",
            hint="Retry later or send a smaller input.",
            retryable=True,
            details=upstream,
        )
    if status == 413:
        return BodhanToolError(
            "input_too_large",
            f"Bodhan {model} rejected the payload size: {detail}",
            hint="Send a smaller input.",
            details=upstream,
        )
    if status == 415:
        return BodhanToolError(
            "unsupported_file",
            f"Bodhan {model} rejected the file format: {detail}",
            details=upstream,
        )
    if 400 <= status < 500:
        return BodhanToolError(
            "invalid_input",
            f"Bodhan {model} rejected the request (HTTP {status}): {detail}",
            details=upstream,
        )
    if status == 502 and _VENDOR_REJECTED in message.lower():
        return BodhanToolError(
            "upstream_error",
            f"Bodhan {model} refused this input: {detail}",
            hint="Not a temporary failure: the audio is likely too long (over about 45 s) or unreadable.",
            details=upstream,
        )
    if status == 502 and model == "ocr" and _OCR_TRUNCATED in message.lower():
        upstream["truncated"] = True
        return BodhanToolError(
            "incomplete_result",
            f"Bodhan ocr could not finish part of the page: {detail}",
            hint="A block was longer than max_tokens; retry with a larger max_tokens.",
            details=upstream,
        )
    if status == 502 and model == "ocr" and _OCR_UNDECODABLE in message.lower():
        return BodhanToolError(
            "unsupported_file",
            f"Bodhan ocr could not decode the image: {detail}",
            hint="Send a valid PNG or JPEG page image.",
            details=upstream,
        )
    if status >= 500:
        return BodhanToolError(
            "upstream_error",
            f"Bodhan {model} failed (HTTP {status}): {detail}",
            hint="Temporary server error; retry later.",
            retryable=True,
            details=upstream,
        )
    return BodhanToolError(
        "upstream_error",
        f"Bodhan {model} returned an unexpected HTTP {status}.",
        details=upstream,
    )


def _completion_tokens(body: dict[str, Any]) -> int | None:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("completion_tokens")
    if type(value) is not int or value < 0:
        return None
    return value


_NOT_SENT = (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError, httpx.ProxyError)
_PARTIALLY_SENT = (httpx.WriteTimeout, httpx.WriteError)
_SENT = (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError, asyncio.TimeoutError)
_TIMEOUTS = (httpx.TimeoutException, asyncio.TimeoutError)


def _network_error(model: str, exc: BaseException, timeout_s: float, retry_if_sent: bool) -> BodhanToolError:
    code: ErrorCode = "timeout" if isinstance(exc, _TIMEOUTS) else "upstream_error"
    kind = exc.__class__.__name__
    if isinstance(exc, _NOT_SENT):
        return BodhanToolError(
            code,
            f"Could not deliver the request to Bodhan {model} ({kind}).",
            hint="Check network access to the Bodhan API; safe to retry.",
            retryable=True,
            details={"request_sent": "no", "possibly_billed": False},
        )
    if isinstance(exc, _PARTIALLY_SENT):
        return BodhanToolError(
            code,
            f"Sending the request to Bodhan {model} failed part-way ({kind}).",
            hint="Retry later or send a smaller input.",
            retryable=True,
            details={"request_sent": "partial", "possibly_billed": False},
        )
    if isinstance(exc, _SENT):
        message = (
            f"Bodhan {model} did not answer within {timeout_s:.0f}s ({kind})."
            if code == "timeout"
            else f"Connection to Bodhan {model} broke while waiting for the answer ({kind})."
        )
        hint = (
            "Retry later or send a smaller input."
            if retry_if_sent
            else "The request may already have been processed and billed, so it was not retried automatically."
        )
        return BodhanToolError(
            code,
            message,
            hint=hint,
            retryable=retry_if_sent,
            details={"request_sent": "yes", "possibly_billed": True},
        )
    return BodhanToolError(
        "upstream_error",
        f"Request to Bodhan {model} failed ({kind}).",
        hint="Check BODHAN_BASE_URL and network settings.",
        details={"request_sent": "unknown", "possibly_billed": None},
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class _CallTracker:
    model: str
    endpoint: str
    started_at: str = field(default_factory=_utc_now)
    started: float = field(default_factory=perf_counter)
    attempts: int = 0
    in_flight: bool = False
    http_status: int | None = None
    possibly_billed_failed_attempts: int = 0
    unknown_delivery_attempts: int = 0
    rate_limit_wait_s: float = 0.0
    server_errors: int = 0
    recorded: bool = False

    def note_failure(self, error: BodhanToolError) -> None:
        upstream_status = error.details.get("upstream_status")
        if isinstance(upstream_status, int):
            self.http_status = upstream_status
        sent = error.details.get("request_sent")
        if sent == "yes":
            self.possibly_billed_failed_attempts += 1
        elif sent == "unknown":
            self.unknown_delivery_attempts += 1

    def note_cancelled(self) -> None:
        if self.in_flight:
            self.possibly_billed_failed_attempts += 1
            self.in_flight = False

    def record(
        self,
        *,
        error: BodhanToolError | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
        estimated_cost_inr: float | None = None,
    ) -> None:
        if self.recorded:
            return
        self.recorded = True
        record_call(
            self._finish(
                error=error,
                error_code=error_code,
                request_id=request_id,
                estimated_cost_inr=estimated_cost_inr,
            )
        )

    def _finish(
        self,
        *,
        error: BodhanToolError | None,
        error_code: str | None,
        request_id: str | None,
        estimated_cost_inr: float | None,
    ) -> CallRecord:
        if error is not None:
            error_code = error.code
            if request_id is None:
                request_id = error.details.get("request_id")
        failed = error_code is not None
        answered = self.http_status is not None and 200 <= self.http_status < 300
        if failed and not answered:
            estimated_cost_inr = 0.0
        return CallRecord(
            model=self.model,
            endpoint=self.endpoint,
            started_at=self.started_at,
            latency_ms=round((perf_counter() - self.started) * 1000, 1),
            attempts=self.attempts,
            outcome="error" if failed else "ok",
            http_status=self.http_status,
            error_code=error_code,
            request_id=request_id,
            estimated_cost_inr=estimated_cost_inr,
            billing_uncertain=(
                estimated_cost_inr is None
                or self.possibly_billed_failed_attempts > 0
                or self.unknown_delivery_attempts > 0
            ),
            possibly_billed_failed_attempts=self.possibly_billed_failed_attempts,
            rate_limit_wait_s=round(self.rate_limit_wait_s, 3),
        )


class BodhanClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._owns_http = http is None
        self._http = http if http is not None else httpx.AsyncClient(timeout=settings.timeout_s)
        self._sleep = sleep
        self._cache = ResultCache(settings.cache_dir, settings.cache_ttl_s)
        self._semaphores = {model: asyncio.Semaphore(settings.max_concurrency) for model in API_KEY_ENV}
        self._rate_limiters = {
            model: RateLimiter(
                settings.speak_requests_per_minute if model == "speak" else settings.requests_per_minute,
                sleep=sleep,
            )
            for model in API_KEY_ENV
        }

    async def __aenter__(self) -> BodhanClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _headers(self, model: str) -> dict[str, str]:
        key = api_key(model)
        if not key:
            raise BodhanToolError(
                "missing_api_key",
                f"No API key configured for the {model} model.",
                hint=f"Set {api_key_hint(model)} in the server's environment.",
            )
        return {"Authorization": f"Bearer {key}"}

    async def _send_once(
        self,
        tracker: _CallTracker,
        headers: dict[str, str],
        retry_if_sent: bool,
        **request: Any,
    ) -> httpx.Response:
        model = tracker.model
        url = f"{self.settings.base_url}{tracker.endpoint}"
        tracker.rate_limit_wait_s += await self._rate_limiters[model].acquire()
        try:
            try:
                async with self._semaphores[model]:
                    tracker.attempts += 1
                    tracker.in_flight = True
                    response = await asyncio.wait_for(
                        self._http.post(url, headers=headers, follow_redirects=False, **request),
                        timeout=self.settings.timeout_s,
                    )
                    tracker.in_flight = False
            except (httpx.TransportError, asyncio.TimeoutError) as exc:
                tracker.in_flight = False
                raise _network_error(model, exc, self.settings.timeout_s, retry_if_sent) from exc
            if not 200 <= response.status_code < 300:
                error = _status_error(model, response)
                if response.status_code == 500 and model in _AMBIGUOUS_500_MODELS:
                    tracker.server_errors += 1
                    if tracker.server_errors > 1:
                        error.retryable = False
                        error.hint = "Bodhan returns this same error for invalid input; it was retried once only."
                raise error
        except BodhanToolError as error:
            tracker.note_failure(error)
            raise
        except asyncio.CancelledError:
            tracker.note_cancelled()
            raise
        tracker.http_status = response.status_code
        return response

    async def _post(
        self,
        model: str,
        path: str,
        *,
        deadline_s: float | None = None,
        retry_if_sent: bool = True,
        **request: Any,
    ) -> tuple[httpx.Response, _CallTracker]:
        headers = self._headers(model)
        tracker = _CallTracker(model=model, endpoint=path)
        try:
            response = await with_retries(
                lambda: self._send_once(tracker, headers, retry_if_sent, **request),
                max_retries=self.settings.max_retries,
                label=f"{model} {path}",
                max_elapsed_s=deadline_s,
                sleep=self._sleep,
            )
        except BodhanToolError as error:
            tracker.record(error=error)
            raise
        except asyncio.CancelledError:
            tracker.note_cancelled()
            tracker.record(error_code="cancelled")
            raise
        except BaseException:
            tracker.note_cancelled()
            tracker.record(error_code="internal_error")
            raise
        return response, tracker

    @staticmethod
    def _json(model: str, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise BodhanToolError(
                "upstream_error",
                f"Bodhan {model} returned a non-JSON response.",
                details={"request_id": _request_id(response)},
            ) from exc
        if not isinstance(body, dict):
            raise BodhanToolError(
                "upstream_error",
                f"Bodhan {model} returned an unexpected response shape.",
                details={"request_id": _request_id(response)},
            )
        error = body.get("error")
        if error:
            code = str(error.get("code") or "") if isinstance(error, dict) else ""
            message = str(error.get("message") or "") if isinstance(error, dict) else str(error)
            raise BodhanToolError(
                "upstream_error",
                f"Bodhan {model} reported an error in a successful response: {message[:300] or 'no message'}",
                details={
                    "upstream_status": response.status_code,
                    "upstream_code": code or None,
                    "request_id": _request_id(response, body),
                },
            )
        return body

    def _record_cache_hit(self, model: str, endpoint: str, request_id: str | None) -> None:
        record_call(
            CallRecord(
                model=model,
                endpoint=endpoint,
                started_at=_utc_now(),
                latency_ms=0.0,
                attempts=0,
                outcome="ok",
                http_status=None,
                error_code=None,
                request_id=request_id,
                estimated_cost_inr=0.0,
                billing_uncertain=False,
                possibly_billed_failed_attempts=0,
                rate_limit_wait_s=0.0,
                cached=True,
            )
        )

    async def _cached(
        self,
        model: str,
        endpoint: str,
        request: dict[str, Any],
        suffix: str,
        encode: Callable[[T], bytes | None],
        decode: Callable[[bytes], T],
        call: Callable[[], Awaitable[T]],
    ) -> T:
        key = cache_key(model, request)
        stored = self._cache.load(model, key, suffix)
        if stored is not None:
            try:
                result = decode(stored)
            except (ValueError, KeyError, TypeError):
                result = None
            if result is not None:
                self._record_cache_hit(model, endpoint, getattr(result, "request_id", None))
                return result

        async def compute() -> T:
            fresh = await call()
            data = encode(fresh)
            if data is not None:
                self._cache.store(model, key, suffix, data)
            return fresh

        result, sent = await self._cache.single_flight(key, compute)
        if not sent:
            self._record_cache_hit(model, endpoint, getattr(result, "request_id", None))
        return result

    async def translate(
        self,
        text: str,
        target_language: str,
        *,
        script: TranslateScript = "native",
        deadline_s: float | None = None,
    ) -> TranslateResult:
        def encode(result: TranslateResult) -> bytes | None:
            if result.truncated:
                return None
            return _dump(
                {"text": result.text, "completion_tokens": result.completion_tokens, "request_id": result.request_id}
            )

        def decode(data: bytes) -> TranslateResult:
            stored = json.loads(data)
            if not isinstance(stored["text"], str):
                raise ValueError("cached translation has no text")
            return TranslateResult(
                text=stored["text"],
                target_language=target_language,
                script=script,
                truncated=False,
                completion_tokens=stored.get("completion_tokens"),
                request_id=stored.get("request_id"),
            )

        return await self._cached(
            "translate",
            "/v1/chat/completions",
            {"text": text, "target_language": target_language, "script": script},
            ".json",
            encode,
            decode,
            lambda: self._translate_call(text, target_language, script, deadline_s),
        )

    async def _translate_call(
        self, text: str, target_language: str, script: TranslateScript, deadline_s: float | None
    ) -> TranslateResult:
        payload: dict[str, Any] = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "target_language": target_language,
        }
        if script != "native":
            payload["script"] = script

        response, tracker = await self._post("translate", "/v1/chat/completions", deadline_s=deadline_s, json=payload)
        request_id = _request_id(response)
        try:
            body = self._json("translate", response)
            request_id = _request_id(response, body)
            result = _parse_translation(body, target_language, script, request_id)
        except BodhanToolError as error:
            tracker.record(error=error, request_id=request_id)
            raise
        tracker.record(request_id=request_id, estimated_cost_inr=translate_cost(result.completion_tokens))
        return result

    async def transcribe(
        self,
        audio: Path,
        audio_seconds: float,
        *,
        language: str | None = None,
        vad: bool = False,
        deadline_s: float | None = None,
    ) -> TranscribeResult:
        content = await asyncio.to_thread(audio.read_bytes)

        def decode(data: bytes) -> TranscribeResult:
            stored = json.loads(data)
            if not isinstance(stored["text"], str):
                raise ValueError("cached transcript has no text")
            return TranscribeResult(text=stored["text"], request_id=stored.get("request_id"))

        return await self._cached(
            "transcribe",
            "/v1/audio/transcriptions",
            {"audio": content_digest(content), "language": language, "vad": vad},
            ".json",
            lambda result: _dump({"text": result.text, "request_id": result.request_id}),
            decode,
            lambda: self._transcribe_call(audio.name, content, audio_seconds, language, vad, deadline_s),
        )

    async def _transcribe_call(
        self,
        name: str,
        content: bytes,
        audio_seconds: float,
        language: str | None,
        vad: bool,
        deadline_s: float | None,
    ) -> TranscribeResult:
        data: dict[str, str] = {"model": "indic-transcribe"}
        if language:
            data["language"] = language
        if vad:
            data["vad"] = "true"

        response, tracker = await self._post(
            "transcribe",
            "/v1/audio/transcriptions",
            deadline_s=deadline_s,
            data=data,
            files={"file": (name, content, "audio/wav")},
        )
        request_id = _request_id(response)
        try:
            body = self._json("transcribe", response)
            request_id = _request_id(response, body)
            text = body.get("text")
            if not isinstance(text, str):
                raise BodhanToolError(
                    "upstream_error",
                    "Bodhan transcribe returned no transcript text.",
                    details={"request_id": request_id},
                )
        except BodhanToolError as error:
            tracker.record(error=error, request_id=request_id)
            raise
        tracker.record(request_id=request_id, estimated_cost_inr=transcribe_cost(audio_seconds))
        return TranscribeResult(text=text, request_id=request_id)

    async def speak(
        self,
        text: str,
        voice: str,
        language: str,
        *,
        style: str | None = None,
        deadline_s: float | None = None,
    ) -> SynthesizeResult:
        def decode(data: bytes) -> SynthesizeResult:
            if sniff_audio_format(data[:16]) != "wav" or len(data) <= 44:
                raise ValueError("cached speech is not WAV audio")
            return SynthesizeResult(audio=data)

        return await self._cached(
            "speak",
            "/v1/audio/speech",
            {"text": text, "voice": voice, "language": language, "style": style},
            ".wav",
            lambda result: result.audio,
            decode,
            lambda: self._speak_call(text, voice, language, style, deadline_s),
        )

    async def _speak_call(
        self, text: str, voice: str, language: str, style: str | None, deadline_s: float | None
    ) -> SynthesizeResult:
        instructions: dict[str, str] = {"lang": language}
        if style:
            instructions["style"] = style
        payload = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": json.dumps(instructions, ensure_ascii=False),
        }
        response, tracker = await self._post(
            "speak", "/v1/audio/speech", deadline_s=deadline_s, retry_if_sent=False, json=payload
        )
        request_id = _request_id(response)
        audio = response.content
        if sniff_audio_format(audio[:16]) != "wav" or len(audio) <= 44:
            error = BodhanToolError(
                "upstream_error",
                "Bodhan speak did not return WAV audio.",
                hint="Retry later; if it persists, try a shorter text.",
                retryable=True,
                details={"content_type": response.headers.get("content-type"), "bytes": len(audio)},
            )
            tracker.record(error=error, request_id=request_id)
            raise error
        tracker.record(request_id=request_id, estimated_cost_inr=speak_cost(len(text)))
        return SynthesizeResult(audio=audio, request_id=request_id)

    async def ocr(
        self,
        image: Path,
        *,
        table_format: str = "markdown",
        max_tokens: int | None = None,
        deadline_s: float | None = None,
    ) -> OcrResult:
        content = await asyncio.to_thread(image.read_bytes)
        mime = _IMAGE_MIME.get(sniff_document(content[:16]) or "")
        if mime is None:
            raise BodhanToolError(
                "unsupported_file",
                f"{image.name} is not a PNG or JPEG image.",
                hint="Convert the page to PNG or JPEG first.",
            )

        def decode(data: bytes) -> OcrResult:
            stored = json.loads(data)
            if not isinstance(stored["markdown"], str) or not isinstance(stored["blocks"], list):
                raise ValueError("cached OCR result is incomplete")
            return OcrResult(markdown=stored["markdown"], blocks=stored["blocks"], request_id=stored.get("request_id"))

        return await self._cached(
            "ocr",
            "/v1/chat/completions",
            {"image": content_digest(content), "table_format": table_format, "max_tokens": max_tokens},
            ".json",
            lambda result: _dump(
                {"markdown": result.markdown, "blocks": result.blocks, "request_id": result.request_id}
            ),
            decode,
            lambda: self._ocr_call(image.name, content, mime, table_format, max_tokens, deadline_s),
        )

    async def _ocr_call(
        self,
        name: str,
        content: bytes,
        mime: str,
        table_format: str,
        max_tokens: int | None,
        deadline_s: float | None,
    ) -> OcrResult:
        payload: dict[str, Any] = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(content).decode()}"},
                        }
                    ],
                }
            ],
            "table_format": table_format,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        response, tracker = await self._post("ocr", "/v1/chat/completions", deadline_s=deadline_s, json=payload)
        request_id = _request_id(response)
        try:
            if response.content.strip() in (b"", b"null"):
                raise BodhanToolError(
                    "unsupported_file",
                    f"Bodhan ocr could not decode {name} and returned no result.",
                    hint="Send a valid PNG or JPEG page image.",
                    details={"request_id": request_id},
                )
            body = self._json("ocr", response)
            request_id = _request_id(response, body)
            result = _parse_ocr(body, request_id)
        except BodhanToolError as error:
            tracker.record(error=error, request_id=request_id)
            raise
        tracker.record(request_id=request_id, estimated_cost_inr=ocr_cost(1))
        return result


def _dump(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _parse_translation(
    body: dict[str, Any],
    target_language: str,
    script: TranslateScript,
    request_id: str | None,
) -> TranslateResult:
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        raise BodhanToolError(
            "upstream_error",
            "Bodhan translate returned an unexpected response shape.",
            details={"request_id": request_id},
        ) from exc
    if not isinstance(content, str):
        raise BodhanToolError(
            "upstream_error",
            "Bodhan translate returned no translation text.",
            details={"request_id": request_id},
        )
    return TranslateResult(
        text=content,
        target_language=target_language,
        script=script,
        truncated=finish_reason == "length",
        completion_tokens=_completion_tokens(body),
        request_id=request_id,
    )


def _parse_ocr(body: dict[str, Any], request_id: str | None) -> OcrResult:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BodhanToolError(
            "upstream_error",
            "Bodhan ocr returned an unexpected response shape.",
            details={"request_id": request_id},
        ) from exc
    if not isinstance(content, str):
        raise BodhanToolError(
            "upstream_error",
            "Bodhan ocr returned no page text.",
            details={"request_id": request_id},
        )
    raw_blocks = body.get("blocks")
    blocks = [block for block in raw_blocks if isinstance(block, dict)] if isinstance(raw_blocks, list) else []
    return OcrResult(markdown=content, blocks=blocks, request_id=request_id)
