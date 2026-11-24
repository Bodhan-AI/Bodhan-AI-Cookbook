"""The single place every Bodhan model call goes through.

All three models share the same transport rules, so they share one code path:

* **per-model pacing** — each model has its own quota, so each gets its own
  pacer; a translate call must not consume transcribe's budget;
* **retry** with exponential backoff and jitter, honouring ``Retry-After``;
* **cache** lookup before the network, keyed on the exact request;
* **cost**, charged per call in rupees;
* **logging**, one structured line per call, including latency and attempt count.

Handlers above this layer never call ``requests`` directly.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from requests import exceptions as rq_exceptions

from . import languages
from .cache import Cache
from .config import MODEL_ASR, MODEL_MT, MODEL_TTS, Settings
from .cost import CostMeter
from .errors import BodhanAPIError
from .log import event
from .ratelimit import MAX_COOLDOWN_S, ConcurrencyGate, RequestPacer

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

_MODEL_CAPABILITY = {
    MODEL_ASR: "transcribe",
    MODEL_MT: "translate",
    MODEL_TTS: "speak",
}

# Indic text runs roughly 3 characters per BPE token. Only used when the
# response does not report its own usage, so the meter is never blank.
_CHARS_PER_TOKEN = 3


@dataclass
class Result:
    data: dict[str, Any]
    cached: bool
    latency_ms: float
    attempts: int


@dataclass
class SpeechResult:
    audio: bytes
    cached: bool
    latency_ms: float
    attempts: int
    content_type: str = "audio/wav"


class BodhanClient:
    def __init__(
        self,
        settings: Settings,
        logger: Any,
        cache: Cache,
        cost: CostMeter,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.log = logger
        self.cache = cache
        self.cost = cost
        self._fixed_session = session
        self._local = threading.local()
        self._sleep = sleep
        self._gate = ConcurrencyGate(
            settings.max_inflight, initial=2, logger=logger
        )
        self._lock = threading.Lock()
        self._pacers: dict[str, RequestPacer] = {}
        self._rate_limits: dict[str, dict[str, str]] = {}
        # The documented translation surface is POST /translate, but it is not
        # served on every deployment. Latch to the chat transport after a 404.
        self._translate_transport = "documented"

    # -- session ----------------------------------------------------------

    def _session(self) -> requests.Session:
        if self._fixed_session is not None:
            return self._fixed_session
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
        return session

    def stop(self) -> None:
        """Release every waiting worker. Used when a run is interrupted."""
        with self._lock:
            pacers = list(self._pacers.values())
        self._gate.stop()
        for pacer in pacers:
            pacer.stop()

    # -- public calls -----------------------------------------------------

    def transcribe(
        self,
        wav_bytes: bytes,
        *,
        language: str,
        audio_seconds: float,
        filename: str = "chunk.wav",
        stage: str = "asr",
        item: int | None = None,
        segment: int | None = None,
    ) -> Result:
        marker = item if item is not None else segment
        key = Cache.make_key(MODEL_ASR, language, wav_bytes)
        cached = self.cache.get(key)
        if cached is not None:
            self.cost.charge_transcribe(audio_seconds, stage=stage, cached=True)
            event(
                self.log,
                "asr.call",
                stage=stage,
                segment=marker,
                cached=True,
                latency_ms=0,
                audio_s=round(audio_seconds, 3),
            )
            data = cached.data if isinstance(cached.data, dict) else {"text": str(cached.data)}
            return Result(data=data, cached=True, latency_ms=0.0, attempts=0)

        body, _, latency_ms, attempts = self._send(
            model=MODEL_ASR,
            path="/v1/audio/transcriptions",
            files={"file": (filename, wav_bytes, "audio/wav")},
            form={"model": MODEL_ASR, "language": language},
            stage=stage,
            item=marker,
            event_name="asr.call",
        )
        data = _parse_json(body, "transcription")
        text = data.get("text")
        if text is None:
            raise BodhanAPIError(
                200,
                "malformed_response",
                f"transcription response had no 'text' field (keys: {sorted(data)})",
            )
        payload = {"text": str(text)}
        for extra in ("segments", "words", "language", "duration"):
            if extra in data:
                payload[extra] = data[extra]

        self.cache.put(
            key,
            payload,
            meta={"model": MODEL_ASR, "language": language, "audio_seconds": round(audio_seconds, 3)},
        )
        self.cost.charge_transcribe(audio_seconds, stage=stage)
        return Result(data=payload, cached=False, latency_ms=latency_ms, attempts=attempts)

    def translate(
        self,
        text: str,
        *,
        target_language: str,
        script: str | None = None,
        max_output_tokens: int | None = None,
        stage: str = "translate",
        item: int | None = None,
    ) -> Result:
        key = Cache.make_key(MODEL_MT, target_language, script or "", text)
        cached = self.cache.get(key)
        if cached is not None:
            translation = str((cached.data or {}).get("translation", ""))
            tokens = int((cached.meta or {}).get("output_tokens", _estimate_tokens(translation)))
            self.cost.charge_translate(tokens, stage=stage, cached=True)
            event(self.log, "mt.call", stage=stage, item=item, cached=True, chars=len(text))
            return Result(
                data={"translation": translation},
                cached=True,
                latency_ms=0.0,
                attempts=0,
            )

        body: dict[str, Any] = {"text": text, "target_language": target_language}
        if script:
            body["script"] = script
        if max_output_tokens:
            body["max_output_tokens"] = max_output_tokens

        with self._lock:
            transport = self._translate_transport

        truncated = False
        try:
            if transport == "documented":
                payload_bytes, _, latency_ms, attempts = self._send(
                    model=MODEL_MT,
                    path="/translate",
                    json_body=body,
                    stage=stage,
                    item=item,
                    event_name="mt.call",
                )
                data = _parse_json(payload_bytes, "translation")
                value = data.get("translation")
                if value is None:
                    raise BodhanAPIError(
                        200,
                        "malformed_response",
                        f"translation response had no 'translation' field (keys: {sorted(data)})",
                    )
                translation = str(value)
                tokens = _usage_tokens(data) or _estimate_tokens(translation)
                truncated = bool(data.get("truncated", False))
            else:
                translation, tokens, latency_ms, attempts, truncated = self._translate_via_chat(
                    text,
                    target_language=target_language,
                    script=script,
                    stage=stage,
                    item=item,
                )
        except BodhanAPIError as exc:
            if exc.status != 404 or transport != "documented":
                raise
            self._latch_chat_transport(stage)
            translation, tokens, latency_ms, attempts, truncated = self._translate_via_chat(
                text,
                target_language=target_language,
                script=script,
                stage=stage,
                item=item,
            )

        if truncated:
            event(
                self.log,
                "mt.truncated",
                level=30,
                stage=stage,
                item=item,
                note="the translation hit its token budget and is incomplete",
            )

        self.cache.put(
            key,
            {"translation": translation},
            meta={
                "model": MODEL_MT,
                "target_language": target_language,
                "output_tokens": tokens,
                "source_chars": len(text),
                "truncated": truncated,
            },
        )
        self.cost.charge_translate(tokens, stage=stage)
        return Result(
            data={
                "translation": translation,
                "output_tokens": tokens,
                "truncated": truncated,
            },
            cached=False,
            latency_ms=latency_ms,
            attempts=attempts,
        )

    def speak(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        style: str | None = None,
        stage: str = "speak",
        item: int | None = None,
    ) -> SpeechResult:
        key = Cache.make_key(MODEL_TTS, language, voice, style or "", text)
        cached = self.cache.get_bytes(key)
        if cached is not None:
            self.cost.charge_speak(len(text), stage=stage, cached=True)
            event(
                self.log,
                "tts.call",
                stage=stage,
                item=item,
                cached=True,
                chars=len(text),
                bytes=len(cached),
            )
            return SpeechResult(audio=cached, cached=True, latency_ms=0.0, attempts=0)

        instructions: dict[str, str] = {"lang": language}
        if style:
            instructions["style"] = style
        body = {
            "model": MODEL_TTS,
            "input": text,
            "voice": voice,
            "instructions": _json_dumps(instructions),
        }

        audio, content_type, latency_ms, attempts = self._send(
            model=MODEL_TTS,
            path="/v1/audio/speech",
            json_body=body,
            stage=stage,
            item=item,
            event_name="tts.call",
            expect_bytes=True,
        )
        if not audio:
            raise BodhanAPIError(200, "empty_audio", "speech response contained no audio bytes")

        self.cache.put_bytes(
            key,
            audio,
            meta={"model": MODEL_TTS, "language": language, "voice": voice, "characters": len(text)},
        )
        self.cost.charge_speak(len(text), stage=stage)
        return SpeechResult(
            audio=audio,
            cached=False,
            latency_ms=latency_ms,
            attempts=attempts,
            content_type=content_type or "audio/wav",
        )

    # -- transport --------------------------------------------------------

    def _latch_chat_transport(self, stage: str) -> None:
        with self._lock:
            if self._translate_transport == "chat":
                return
            self._translate_transport = "chat"
        event(
            self.log,
            "mt.transport_fallback",
            level=30,
            stage=stage,
            note="POST /translate returned 404 on this deployment; "
            "switching to POST /v1/chat/completions for the rest of the run",
        )

    def _translate_via_chat(
        self,
        text: str,
        *,
        target_language: str,
        script: str | None,
        stage: str,
        item: int | None,
    ) -> tuple[str, int, float, int, bool]:
        """The documented second transport: one user turn, canonical prompt.

        The model is prompt-sensitive and degrades silently when the format is
        wrong, so the instruction is built exactly as documented. No system
        turn, no generation settings — the server rejects both, and the target
        language is named, not passed as an ISO code.
        """
        name = languages.prompt_name(target_language, script)
        body: dict[str, Any] = {
            "model": MODEL_MT,
            "messages": [
                {"role": "user", "content": f"Translate the following text into {name}:\n\n{text}"}
            ],
        }

        payload_bytes, _, latency_ms, attempts = self._send(
            model=MODEL_MT,
            path="/v1/chat/completions",
            json_body=body,
            stage=stage,
            item=item,
            event_name="mt.call",
        )
        data = _parse_json(payload_bytes, "translation")
        translation, truncated = _chat_content(data)
        tokens = _usage_tokens(data) or _estimate_tokens(translation)
        return translation, tokens, latency_ms, attempts, truncated

    def _pacer(self, model: str) -> RequestPacer:
        with self._lock:
            pacer = self._pacers.get(model)
            if pacer is None:
                pacer = RequestPacer(
                    self.settings.rpm,
                    base_cooldown_s=max(0.0, self.settings.backoff_base_s),
                    max_cooldown_s=MAX_COOLDOWN_S,
                    logger=self.log,
                )
                self._pacers[model] = pacer
            return pacer

    def _send(
        self,
        *,
        model: str,
        path: str,
        stage: str,
        event_name: str,
        item: int | None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        expect_bytes: bool = False,
    ) -> tuple[bytes, str, float, int]:
        url = f"{self.settings.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.settings.key_for(_MODEL_CAPABILITY[model])}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        pacer = self._pacer(model)
        last_error: BodhanAPIError | None = None

        # Throttling and real failures are counted separately. A busy minute
        # must not consume a chunk's error budget: the chunk is fine, the
        # quota is not. Each 429 is waited out by the shared pacer cooldown.
        attempt = 1
        throttled = 0
        while True:
            # Two independent controls: bound how many calls are in flight, then
            # pace the start of this request. The gate adapts to a parallel-request
            # cap; the pacer adapts to a per-minute quota.
            if not self._gate.acquire():
                raise BodhanAPIError(0, "cancelled", "the run was interrupted before this call")

            started = time.monotonic()
            response: requests.Response | None = None
            transport_error: Exception | None = None
            try:
                if not pacer.acquire():
                    raise BodhanAPIError(
                        0, "cancelled", "the run was interrupted before this call"
                    )
                if json_body is not None:
                    response = self._session().post(
                        url, headers=headers, json=json_body, timeout=self.settings.timeout_s
                    )
                else:
                    response = self._session().post(
                        url,
                        headers=headers,
                        data=form,
                        files=files,
                        timeout=self.settings.timeout_s,
                    )
            except rq_exceptions.RequestException as exc:
                transport_error = exc
            finally:
                self._gate.release()

            elapsed = (time.monotonic() - started) * 1000

            if transport_error is not None:
                last_error = BodhanAPIError(
                    0,
                    "transport",
                    f"{type(transport_error).__name__}: {transport_error}",
                    retryable=True,
                )
                event(
                    self.log,
                    f"{event_name}.retry",
                    level=30,
                    stage=stage,
                    item=item,
                    attempt=attempt,
                    reason="transport",
                    latency_ms=round(elapsed, 1),
                )
                if attempt >= self.settings.max_attempts:
                    break
                attempt += 1
                self._sleep(self._backoff_delay(attempt))
                continue

            assert response is not None
            self._capture_rate_limits(model, response.headers)

            if response.ok:
                pacer.on_success()
                self._gate.on_success()
                return (
                    response.content,
                    response.headers.get("content-type", ""),
                    elapsed,
                    attempt,
                )

            error = _parse_error(response)
            last_error = error

            if response.status_code == 429:
                throttled += 1
                self._gate.on_throttled()
                pacer.on_rate_limited()
                event(
                    self.log,
                    f"{event_name}.retry",
                    level=30,
                    stage=stage,
                    item=item,
                    attempt=attempt,
                    throttle=throttled,
                    reason="rate_limited",
                    latency_ms=round(elapsed, 1),
                )
                if throttled > self.settings.max_throttle_retries:
                    raise error
                continue

            if response.status_code not in RETRYABLE_STATUS:
                raise error

            event(
                self.log,
                f"{event_name}.retry",
                level=30,
                stage=stage,
                item=item,
                attempt=attempt,
                reason=error.code,
                status=error.status,
                latency_ms=round(elapsed, 1),
            )
            if attempt >= self.settings.max_attempts:
                break
            attempt += 1
            self._sleep(_retry_after(response) or self._backoff_delay(attempt))

        assert last_error is not None
        raise last_error

    def _backoff_delay(self, attempt: int) -> float:
        base = self.settings.backoff_base_s * (2 ** (attempt - 1))
        capped = min(base, self.settings.backoff_cap_s)
        return capped * (0.75 + random.random() * 0.5)

    def _capture_rate_limits(self, model: str, headers: Any) -> None:
        found = {
            key.lower(): value
            for key, value in headers.items()
            if key.lower().startswith("x-ratelimit-")
        }
        if not found:
            return
        with self._lock:
            self._rate_limits[model] = found
        ceiling = found.get("x-ratelimit-limit-requests")
        if ceiling:
            try:
                self._pacer(model).set_ceiling(int(float(ceiling)))
            except ValueError:
                pass

    def rate_limits(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return {model: dict(values) for model, values in self._rate_limits.items()}

    def limiter_stats(self) -> dict[str, Any]:
        with self._lock:
            pacers = dict(self._pacers)
        return {
            model: {**pacer.stats(), "concurrency": self._gate.stats()}
            for model, pacer in pacers.items()
        }


def _parse_json(body: bytes, what: str) -> dict[str, Any]:
    import json

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BodhanAPIError(200, "malformed_json", f"{what} response was not JSON") from exc
    if not isinstance(payload, dict):
        raise BodhanAPIError(200, "malformed_json", f"{what} response JSON was not an object")
    return payload


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _chat_content(payload: dict[str, Any]) -> tuple[str, bool]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise BodhanAPIError(
            200, "malformed_response", "chat-completions response had no 'choices'"
        )
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    content = message.get("content")
    if content is None:
        raise BodhanAPIError(
            200, "malformed_response", "chat-completions response had no message content"
        )
    truncated = str(choice.get("finish_reason") or "") == "length"
    return str(content).strip(), truncated


def _usage_tokens(payload: dict[str, Any]) -> int:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0
    for key in ("output_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


def _parse_error(response: requests.Response) -> BodhanAPIError:
    code = f"http_{response.status_code}"
    message = response.text[:300] if response.text else response.reason or "no message"
    request_id = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
            request_id = error.get("request_id")
        elif isinstance(error, str):
            message = error
    return BodhanAPIError(
        response.status_code,
        code,
        message,
        request_id=str(request_id) if request_id else None,
        retryable=response.status_code in RETRYABLE_STATUS,
    )


def _retry_after(response: requests.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
