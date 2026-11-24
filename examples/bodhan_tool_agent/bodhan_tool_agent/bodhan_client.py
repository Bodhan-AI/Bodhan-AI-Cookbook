
from __future__ import annotations

import base64
import email.utils
import json
import math
import mimetypes
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .audio import (
    MAX_TRANSCRIPTION_SECONDS,
    AudioProcessingError,
    audio_duration_seconds,
)
from .config import Settings
from .text import (
    MAX_TRANSLATION_TEXT_CHARACTERS,
    TextChunkingError,
    split_text,
)

DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS = 1024


class BodhanError(Exception):
    """Base exception for Bodhan client failures."""


class TranslationChunkError(BodhanError):
    """Raised when a later text chunk fails after earlier chunks succeed."""

    def __init__(
        self,
        chunk_index: int,
        chunk_count: int,
        partial_translation: str,
        completed_chunk_count: int,
        usage: list[dict[str, Any]],
        cause: Exception,
    ) -> None:
        super().__init__(
            f"Translation failed at chunk {chunk_index} of {chunk_count}"
        )
        self.chunk_index = chunk_index
        self.chunk_count = chunk_count
        self.partial_translation = partial_translation
        self.completed_chunk_count = completed_chunk_count
        self.usage = usage
        self.cause = cause


class BodhanNetworkError(BodhanError):
    """Raised after a transient network request is exhausted."""


class BodhanUncertainOutcomeError(BodhanNetworkError):
    """Raised when a paid network timeout leaves provider outcome uncertain."""


class BodhanDeadlineExceeded(BodhanError):
    """Raised when no task or request deadline remains for another attempt."""


class BodhanResponseError(BodhanError):
    """Raised when Bodhan returns a non-success response."""


class BodhanAPIError(BodhanResponseError):
    """Raised for Bodhan's structured API error envelope."""

    def __init__(
        self,
        status_code: int,
        message: str,
        code: str | None = None,
        request_id: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after


class TranslationTruncatedError(BodhanResponseError):
    """Raised when a successful translation hit an output budget."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("Translation was truncated and may be incomplete")
        self.result = result
        self.translation = result.get("translation")
        self.truncated = True
        self.usage = result.get("usage")
        self.partial_translation = (
            result.get("partial_translation")
            if isinstance(result.get("partial_translation"), str)
            else result.get("translation")
            if isinstance(result.get("translation"), str)
            else ""
        )
        self.completed_chunk_count = result.get("completed_chunk_count")
        self.chunk_index = result.get("chunk_index")
        self.chunk_count = result.get("chunk_count")
        self.partial = result.get("partial", True)


class BodhanClient:
    """Shared HTTP client for the four supported Bodhan model IDs."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
        max_wait: float = 30.0,
        sleep: Any = time.sleep,
        deadline_epoch: float | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        if backoff_factor < 0:
            raise ValueError("backoff_factor must be zero or greater")
        if max_wait < 0 or not math.isfinite(max_wait):
            raise ValueError("max_wait must be a finite non-negative number")

        self.settings = settings
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_wait = max_wait
        self._sleep = sleep
        if deadline_epoch is None:
            raw_deadline = os.environ.get("BODHAN_TASK_DEADLINE_EPOCH")
            if raw_deadline:
                try:
                    deadline_epoch = float(raw_deadline)
                except (TypeError, ValueError):
                    deadline_epoch = None
        self.deadline_epoch = deadline_epoch
        self.last_retry_stats: dict[str, Any] = {
            "retry_attempts": 0,
            "retry_succeeded": False,
            "retry_exhausted": False,
            "uncertain_paid_outcome": False,
            "failure_category": "none",
        }

    def transcribe(self, file_path: str | Path, language: str | None = None) -> str:
        path = Path(file_path)
        if not path.is_file():
            raise BodhanResponseError(f"Audio file does not exist: {path}")

        try:
            duration = audio_duration_seconds(path)
        except AudioProcessingError:
            duration = None
        if duration is not None and duration > MAX_TRANSCRIPTION_SECONDS:
            raise BodhanResponseError(
                "Audio exceeds the 30-second transcription limit; "
                "split the file into chunks before calling this client"
            )

        data: dict[str, str] = {"model": "indic-transcribe"}
        if language:
            data["language"] = language

        with path.open("rb") as audio:
            response = self._request(
                "indic-transcribe",
                "POST",
                "/v1/audio/transcriptions",
                files={
                    "file": (
                        path.name,
                        audio,
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream",
                    )
                },
                data=data,
            )

        payload = self._json(response)
        text = payload.get("text")
        if not isinstance(text, str):
            raise BodhanResponseError(
                "Bodhan transcription response did not contain a string 'text' field"
            )
        return text

    def translate(
        self,
        text: str,
        target_language: str,
        *,
        script: str | None = None,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        if not text:
            raise ValueError("text must not be empty")
        if not target_language:
            raise ValueError("target_language must not be empty")
        effective_max_output_tokens = (
            DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS
            if max_output_tokens is None
            else max_output_tokens
        )
        if (
            isinstance(effective_max_output_tokens, bool)
            or not isinstance(effective_max_output_tokens, int)
            or effective_max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be greater than zero")

        try:
            chunks = split_text(
                text,
                MAX_TRANSLATION_TEXT_CHARACTERS,
            )
        except TextChunkingError as exc:
            raise ValueError(str(exc)) from exc

        translated_chunks: list[str] = []
        chunk_usage: list[dict[str, Any]] = []
        aggregate_usage: dict[str, Any] = {}
        aggregate_retry_attempts = 0
        aggregate_retry_succeeded = False
        aggregate_retry_exhausted = False
        aggregate_uncertain = False

        for index, chunk in enumerate(chunks):
            try:
                result = self._translate_chunk(
                    chunk.text,
                    target_language,
                    script=script,
                    max_output_tokens=effective_max_output_tokens,
                )
                retry_stats = dict(self.last_retry_stats)
                aggregate_retry_attempts += int(
                    retry_stats.get("retry_attempts", 0) or 0
                )
                aggregate_retry_succeeded = (
                    aggregate_retry_succeeded
                    or bool(retry_stats.get("retry_succeeded", False))
                )
                aggregate_retry_exhausted = (
                    aggregate_retry_exhausted
                    or bool(retry_stats.get("retry_exhausted", False))
                )
                aggregate_uncertain = (
                    aggregate_uncertain
                    or bool(retry_stats.get("uncertain_paid_outcome", False))
                )
            except TranslationTruncatedError as exc:
                partial = exc.result
                retry_stats = dict(self.last_retry_stats)
                aggregate_retry_attempts += int(
                    retry_stats.get("retry_attempts", 0) or 0
                )
                aggregate_retry_succeeded = (
                    aggregate_retry_succeeded
                    or bool(retry_stats.get("retry_succeeded", False))
                )
                aggregate_retry_exhausted = (
                    aggregate_retry_exhausted
                    or bool(retry_stats.get("retry_exhausted", False))
                )
                aggregate_uncertain = (
                    aggregate_uncertain
                    or bool(retry_stats.get("uncertain_paid_outcome", False))
                )
                partial_translation = (
                    partial.get("translation")
                    if isinstance(partial.get("translation"), str)
                    else ""
                )
                truncated_usage = (
                    partial.get("usage", {})
                    if isinstance(partial.get("usage", {}), dict)
                    else {}
                )
                chunk_usage.append(truncated_usage)
                self._merge_usage(aggregate_usage, truncated_usage)
                partial_result = dict(partial)
                partial_result.update(
                    {
                        "translation": "",
                        "partial_translation": (
                            " ".join(
                                translated_chunks
                                + ([partial_translation] if partial_translation else [])
                            )
                        ),
                        "target_language": target_language,
                        "script": script,
                        "truncated": True,
                        "partial": True,
                        "chunk_index": index + 1,
                        "chunk_count": len(chunks),
                        "completed_chunk_count": index,
                        "chunk_usage": chunk_usage,
                        "chunk_character_counts": [
                            len(item.text) for item in chunks
                        ],
                        "usage": aggregate_usage,
                        "retry_attempts": aggregate_retry_attempts,
                        "retry_succeeded": aggregate_retry_succeeded,
                        "retry_exhausted": aggregate_retry_exhausted,
                        "uncertain_paid_outcome": aggregate_uncertain,
                    }
                )
                raise TranslationTruncatedError(partial_result) from exc
            except BodhanError as exc:
                if len(chunks) == 1:
                    raise
                raise TranslationChunkError(
                    index + 1,
                    len(chunks),
                    " ".join(translated_chunks),
                    index,
                    chunk_usage,
                    exc,
                ) from exc

            translated_chunks.append(result["translation"])
            usage = result.get("usage", {})
            chunk_usage.append(
                usage if isinstance(usage, dict) else {}
            )
            self._merge_usage(aggregate_usage, usage)

        return {
            "translation": " ".join(translated_chunks),
            "target_language": target_language,
            "script": script,
            "truncated": False,
            "partial": False,
            "chunk_count": len(chunks),
            "chunk_character_counts": [len(chunk.text) for chunk in chunks],
            "chunk_usage": chunk_usage,
            "usage": aggregate_usage,
            "retry_attempts": aggregate_retry_attempts,
            "retry_succeeded": aggregate_retry_succeeded,
            "retry_exhausted": aggregate_retry_exhausted,
            "uncertain_paid_outcome": aggregate_uncertain,
        }

    @staticmethod
    def _merge_usage(target: dict[str, Any], usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key, value in usage.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                current = target.get(key)
                if isinstance(current, (int, float)) and not isinstance(current, bool):
                    target[key] = current + value
                elif key not in target:
                    target[key] = value

    def _translate_chunk(
        self,
        text: str,
        target_language: str,
        *,
        script: str | None,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "text": text,
            "target_language": target_language,
        }
        if script is not None:
            body["script"] = script
        body["max_output_tokens"] = max_output_tokens

        response = self._request(
            "indic-translate",
            "POST",
            "/translate",
            json=body,
        )
        payload = self._json(response)

        translation = payload.get("translation")
        if not isinstance(translation, str):
            raise BodhanResponseError(
                "Bodhan translation response did not contain a string 'translation' field"
            )

        # Missing or malformed truncation metadata must never imply success.
        truncated = payload.get("truncated")
        if not isinstance(truncated, bool):
            raise BodhanResponseError(
                "Bodhan translation response did not contain a valid boolean 'truncated' field"
            )

        result = dict(payload)
        result.setdefault("usage", {})

        if truncated:
            raise TranslationTruncatedError(result)

        return result

    def speak(
        self,
        input_text: str,
        voice: str,
        lang: str,
        *,
        style: str | None = None,
    ) -> bytes:
        if not input_text:
            raise ValueError("input_text must not be empty")
        if not voice:
            raise ValueError("voice must not be empty")
        if not lang:
            raise ValueError("lang must not be empty")

        instructions: dict[str, str] = {"lang": lang}
        if style is not None:
            instructions["style"] = style

        response = self._request(
            "indic-speak",
            "POST",
            "/v1/audio/speech",
            json={
                "model": "indic-speak",
                "input": input_text,
                "voice": voice,
                "instructions": json.dumps(instructions, ensure_ascii=False),
            },
        )

        if not response.content:
            raise BodhanResponseError("Bodhan speech response contained no audio")

        content_type = (
            response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        if content_type not in {"audio/wav", "audio/x-wav"}:
            raise BodhanResponseError(
                f"Unexpected Bodhan speech Content-Type: {content_type or 'missing'}"
            )

        return response.content

    def ocr(
        self,
        image_path: str | Path,
        *,
        table_format: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        path = Path(image_path)
        if not path.is_file():
            raise BodhanResponseError(f"Image file does not exist: {path}")

        suffix = path.suffix.lower()
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        if suffix not in mime_types:
            raise ValueError("OCR image must be a PNG or JPEG file")

        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        body: dict[str, Any] = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_types[suffix]};base64,{encoded}"
                            },
                        }
                    ],
                }
            ],
        }

        if table_format is not None:
            body["table_format"] = table_format
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        response = self._request(
            "indic-ocr",
            "POST",
            "/v1/chat/completions",
            json=body,
        )
        payload = self._json(response)

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise BodhanResponseError("Bodhan OCR response did not contain choices")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise BodhanResponseError(
                "Bodhan OCR response did not contain choices[0].message.content"
            )

        blocks = payload.get("blocks", [])
        if not isinstance(blocks, list):
            raise BodhanResponseError(
                "Bodhan OCR response field 'blocks' was not a list"
            )

        usage = payload.get("usage", {})
        if not isinstance(usage, dict):
            raise BodhanResponseError(
                "Bodhan OCR response field 'usage' was not an object"
            )

        return {
            "content": content,
            "blocks": blocks,
            "usage": usage,
        }

    def _request(
        self, model: str, method: str, path: str, **kwargs: Any
    ) -> requests.Response:
        self.last_retry_stats = {
            "retry_attempts": 0,
            "retry_succeeded": False,
            "retry_exhausted": False,
            "uncertain_paid_outcome": False,
            "failure_category": "none",
        }
        base_url = self.settings.base_url.rstrip("/")
        url = f"{base_url}{path}"
        key = self.settings.key_for(model)

        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {key}"

        if kwargs.get("json") is not None:
            headers.setdefault("Content-Type", "application/json")

        last_network_error: requests.RequestException | None = None

        for attempt in range(self.max_retries + 1):
            remaining = self._deadline_remaining()
            if remaining is not None and remaining <= 0:
                self.last_retry_stats["failure_category"] = "deadline"
                raise BodhanDeadlineExceeded(
                    "The task deadline expired before another Bodhan request attempt"
                )
            request_timeout = self.settings.timeout
            if remaining is not None:
                request_timeout = min(request_timeout, remaining)
            if request_timeout <= 0:
                self.last_retry_stats["failure_category"] = "deadline"
                raise BodhanDeadlineExceeded(
                    "The task deadline expired before another Bodhan request attempt"
                )

            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=request_timeout,
                    **kwargs,
                )
            except requests.Timeout as exc:
                if model in {
                    "indic-transcribe",
                    "indic-translate",
                    "indic-speak",
                    "indic-ocr",
                }:
                    self.last_retry_stats["uncertain_paid_outcome"] = True
                    self.last_retry_stats["failure_category"] = "uncertain_paid_timeout"
                raise BodhanUncertainOutcomeError(
                    "Bodhan request timed out; provider outcome is uncertain"
                ) from exc
            except requests.RequestException as exc:
                last_network_error = exc
                if attempt >= self.max_retries:
                    self.last_retry_stats["retry_exhausted"] = True
                    self.last_retry_stats["failure_category"] = "network"
                    break
                delay = self._backoff_delay(attempt, None)
                if not self._sleep_for_retry(delay):
                    self.last_retry_stats["failure_category"] = "deadline"
                    raise BodhanDeadlineExceeded(
                        "Task deadline expires before the next Bodhan retry"
                    ) from None
                self.last_retry_stats["retry_attempts"] += 1
                continue

            if 200 <= response.status_code < 300:
                if attempt > 0:
                    self.last_retry_stats["retry_succeeded"] = True
                return response

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self.max_retries:
                    self.last_retry_stats["retry_exhausted"] = True
                    self.last_retry_stats["failure_category"] = (
                        "http_429" if response.status_code == 429 else "http_5xx"
                    )
                    self._raise_http_error(response)

                retry_after = response.headers.get("Retry-After")
                delay = self._backoff_delay(attempt, retry_after)
                if not self._sleep_for_retry(delay):
                    self.last_retry_stats["failure_category"] = "deadline"
                    raise BodhanDeadlineExceeded(
                        "Task deadline expires before the next Bodhan retry"
                    )
                self.last_retry_stats["retry_attempts"] += 1
                continue

            self.last_retry_stats["failure_category"] = "permanent"
            self._raise_http_error(response)

        raise BodhanNetworkError(
            "Bodhan request failed after bounded network retries"
        ) from last_network_error

    def _deadline_remaining(self) -> float | None:
        if self.deadline_epoch is None:
            return None
        return max(0.0, self.deadline_epoch - time.time())

    def _sleep_for_retry(self, delay: float) -> bool:
        remaining = self._deadline_remaining()
        if remaining is not None and delay >= remaining:
            return False
        self._sleep(delay)
        return True

    def _raise_http_error(self, response: requests.Response) -> None:
        status = response.status_code
        retry_after = response.headers.get("Retry-After")

        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError):
            payload = None

        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                code = error.get("code")
                request_id = error.get("request_id")

                raise BodhanAPIError(
                    status,
                    str(message)
                    if message is not None
                    else f"Bodhan request failed with status {status}",
                    code=str(code) if code is not None else None,
                    request_id=str(request_id) if request_id is not None else None,
                    retry_after=retry_after,
                )

            detail = payload.get("detail")
            if detail:
                raise BodhanAPIError(
                    status,
                    str(detail),
                    retry_after=retry_after,
                )

        reason = response.reason or f"HTTP {status}"
        raise BodhanAPIError(status, reason, retry_after=retry_after)

    def _json(self, response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise BodhanResponseError(
                "Bodhan returned a non-JSON success response"
            ) from exc

        if not isinstance(payload, dict):
            raise BodhanResponseError(
                "Bodhan success response was not a JSON object"
            )

        return payload

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            parsed = retry_after.strip()

            try:
                seconds = float(parsed)
                if seconds >= 0 and math.isfinite(seconds):
                    return min(seconds, self.max_wait)
            except ValueError:
                try:
                    parsed_date = email.utils.parsedate_to_datetime(parsed)
                except (TypeError, ValueError):
                    parsed_date = None

                if parsed_date is not None:
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=timezone.utc)

                    seconds = (
                        parsed_date - datetime.now(timezone.utc)
                    ).total_seconds()

                    if math.isfinite(seconds):
                        return min(max(0.0, seconds), self.max_wait)

        return self.backoff_factor * (2**attempt)
