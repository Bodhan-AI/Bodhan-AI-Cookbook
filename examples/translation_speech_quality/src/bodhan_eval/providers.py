"""Exact-input cache, cost accounting, and hosted Bodhan and judge clients."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from .schema import VOICES

BASE_URL = "https://api.bodhan.ai"
MICRO_INR = 1_000_000
MAX_RETRY_WAIT = 90
# Five attempts ride out a typical per-minute rate-limit window (2+4+8+16 s plus jitter).
ATTEMPTS = 5
# Unusable judge replies are sampled again. Bodhan outputs are never re-requested on validation errors.
RETRYABLE_VERDICTS = {"invalid_judgment", "invalid_response"}
# Fields some OpenAI-compatible judges reject. The request is retried without them.
OPTIONAL_JUDGE_FIELDS = frozenset({"response_format", "temperature"})
LOG = logging.getLogger(__name__)


class CallError(Exception):
    """A check could not run or a provider did not return a usable response."""

    def __init__(self, code: str, message: str):
        """Keep a stable error code alongside the sanitized explanation."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Call:
    """A cached or live response and the usage of the original successful call."""

    key: str
    value: bytes
    usage: dict[str, Any]


def encoded(value: Any) -> bytes:
    """Encode stable UTF-8 JSON for digests and cache metadata."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(data: bytes) -> str:
    """Derive a SHA-256 identifier from actual bytes."""
    return hashlib.sha256(data).hexdigest()


def json_object(data: bytes) -> dict[str, Any]:
    """Decode one provider JSON object or raise an actionable response error."""
    try:
        body = json.loads(data)
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
    except (ValueError, UnicodeError) as error:
        raise CallError("invalid_response", "Provider returned invalid JSON.") from error
    return body


def token_counts(value: Any) -> tuple[int | None, int | None]:
    """Return nonnegative input/output usage when a provider supplies it."""
    if not isinstance(value, dict):
        return None, None
    counts = value.get("prompt_tokens"), value.get("completion_tokens")
    return tuple(count if type(count) is int and count >= 0 else None for count in counts)


def retry_delay(header: str, attempt: int, now: datetime) -> float:
    """Honor numeric and HTTP-date Retry-After values, with exponential backoff as a floor."""
    delay = float(2**attempt)
    if header.isdecimal():
        return max(delay, int(header))
    if header:
        try:
            deadline = parsedate_to_datetime(header)
            if deadline.tzinfo is not None:
                return max(delay, (deadline - now).total_seconds())
        except (ValueError, OverflowError):
            pass
    return delay


class Providers:
    """Own one run's HTTP connection pool, concurrency, cache, and spend ledger."""

    def __init__(
        self,
        cache_path: Path,
        max_micro_inr: int,
        concurrency: int,
        judge_concurrency: int | None = None,
        *,
        judge_rpm: int | None = None,
        max_retry_wait: int = MAX_RETRY_WAIT,
        voices: dict[str, str] | None = None,
        speech_style: str | None = None,
    ):
        """Configure local persistence and read only the assignment's credential variables."""
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache = sqlite3.connect(cache_path, timeout=10)
        self.cache.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, body BLOB, usage TEXT)")
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), trust_env=False)
        self.limit = asyncio.Semaphore(concurrency)
        # The judge often has a tighter rate limit than Bodhan, so it gets its own bound.
        self.judge_limit = asyncio.Semaphore(judge_concurrency or concurrency)
        self.cooldown: dict[str, float] = {}
        # Optional client-side pacing for judges with a low requests-per-minute quota.
        self.judge_interval = 60 / judge_rpm if judge_rpm else 0.0
        self.next_judge_slot = 0.0
        self.max_retry_wait = max_retry_wait
        # Generation settings under comparison. The defaults are the documented voice per language.
        self.voices = {**VOICES, **(voices or {})}
        self.speech_style = speech_style
        self.budget = max_micro_inr
        self.charged = 0
        self.inflight: dict[str, asyncio.Task[Call]] = {}
        self.telemetry: Counter[str] = Counter()
        self.exhausted = set()
        self.keys = {
            name: os.getenv(name, "").strip()
            for name in (
                "BODHAN_TRANSLATE_KEY",
                "BODHAN_SPEAK_KEY",
                "BODHAN_TRANSCRIBE_KEY",
                "LLM_API_KEY",
            )
        }
        self.judge_url = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
        self.judge_model = os.getenv("LLM_MODEL", "").strip()

    async def __aenter__(self) -> Providers:
        """Return the run client to its context manager caller."""
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Cancel outstanding work before closing its HTTP pool and database."""
        pending = list(self.inflight.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self.http.aclose()
        self.cache.close()

    async def _call(
        self,
        operation: str,
        url: str,
        key_name: str,
        payload: dict[str, Any],
        reserve: int,
        validate: Callable[[bytes], tuple[bytes, dict[str, Any]]],
        *,
        wav: bytes | None = None,
        fresh: bool = False,
    ) -> Call:
        """Replay a cached response, share a matching pending call, or dispatch once."""
        # The judge cache predates verdict validation. Keep old unchecked entries
        # out of the new namespace without invalidating cached Bodhan outputs.
        version = "v2" if operation == "judge" else "v1"
        cache_key = digest(encoded([version, operation, url, payload, digest(wav) if wav else None]))
        if fresh:
            # A repeatability trial is a second, independent call stored under its own key.
            # It is cached too, so a replay reproduces the same agreement figure at no cost.
            cache_key = digest(encoded(["repeat", cache_key]))
        row =self.cache.execute("SELECT body, usage FROM responses WHERE key = ?", (cache_key,)).fetchone()
        if row:
            self.telemetry["cache_hits"] += 1
            return Call(cache_key, row[0], json.loads(row[1]))
        task = self.inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(self._request(cache_key, operation, url, key_name, payload, reserve, validate, wav, persist=True))
            self.inflight[cache_key] = task
            task.add_done_callback(lambda _: self.inflight.pop(cache_key, None))
        else:
            self.telemetry["shared_calls"] += 1
        return await asyncio.shield(task)

    async def _request(
        self,
        cache_key: str,
        operation: str,
        url: str,
        key_name: str,
        payload: dict[str, Any],
        reserve: int,
        validate: Callable[[bytes], tuple[bytes, dict[str, Any]]],
        wav: bytes | None,
        *,
        persist: bool,
    ) -> Call:
        """Reserve spend, retry transient errors, and atomically store successful output."""
        token = self.keys[key_name]
        if not token:
            raise CallError("missing_credentials", f"Set {key_name} to use {operation}.")
        error = CallError("unavailable", f"{operation} was not attempted.")
        for attempt in range(ATTEMPTS):
            delay = 2**attempt
            # A rate limit applies to the key, so every caller waits out the same window.
            wait = self.cooldown.get(key_name, 0) - monotonic()
            if wait > self.max_retry_wait:
                # A quota window (often daily) is still closed. Fail fast instead of stalling every row.
                raise CallError("retry_delay", f"{operation} is rate limited for another {round(wait)} s, beyond the {self.max_retry_wait}-second wait limit.")
            if wait > 0:
                await asyncio.sleep(wait)
            if operation == "judge" and self.judge_interval:
                slot = max(monotonic(), self.next_judge_slot)
                self.next_judge_slot = slot + self.judge_interval
                await asyncio.sleep(slot - monotonic())
            limit = self.judge_limit if operation == "judge" else self.limit
            async with limit:
                if key_name in self.exhausted:
                    raise CallError("credit_exhausted", f"Provider credit exhausted for {operation}.")
                if reserve and self.charged + reserve > self.budget:
                    raise CallError("budget", f"Bodhan spend cap prevents {operation}.")
                # Transport failures can occur after billing. Keep their upper bounds in the ledger.
                self.charged += reserve
                self.telemetry["requests"] += 1
                self.telemetry[f"{operation}_requests"] += 1
                started = monotonic()
                try:
                    args = {"data": payload, "files": {"file": ("chunk.wav", wav, "audio/wav")}} if wav else {"json": payload}
                    response = await self.http.post(url, headers={"Authorization": f"Bearer {token}"}, **args)
                except httpx.TransportError:
                    error = CallError("transport", f"{operation} connection failed. Charge may be unknown.")
                else:
                    status = response.status_code
                    LOG.info(
                        "provider_result",
                        extra={
                            "event_fields": {
                                "operation": operation,
                                "request": cache_key[:12],
                                "attempt": attempt + 1,
                                "status_code": status,
                                "request_id": response.headers.get("x-request-id"),
                                "elapsed_ms": round((monotonic() - started) * 1000),
                                # The provider's own explanation, e.g. which rate limit was hit.
                                "detail": None if status == 200 else " ".join(response.text.split())[:240],
                            }
                        },
                    )
                    if status == 200:
                        try:
                            body, usage = validate(response.content)
                        except CallError as invalid:
                            # A judge can return an unusable verdict once and a valid one on
                            # the next sample. Bodhan outputs are deterministic, so only the
                            # judge is asked again.
                            if operation != "judge" or invalid.code not in RETRYABLE_VERDICTS:
                                raise
                            self.telemetry["judge_invalid_retries"] += 1
                            error = invalid
                            if attempt + 1 >= ATTEMPTS:
                                break
                            continue
                        spent = usage.get("micro_inr", reserve)
                        self.charged += spent - reserve
                        usage = {"operation": operation, **usage}
                        if persist:
                            with self.cache:
                                self.cache.execute(
                                    "INSERT OR IGNORE INTO responses VALUES (?, ?, ?)",
                                    (cache_key, body, encoded(usage).decode("utf-8")),
                                )
                        return Call(cache_key, body, usage)
                    if status == 429 and any(word in response.text.lower() for word in ("credit", "balance", "budget", "insufficient_quota")):
                        self.charged -= reserve
                        self.exhausted.add(key_name)
                        raise CallError("credit_exhausted", f"Provider credit exhausted for {operation}.")
                    if status < 500 and status != 408:
                        self.charged -= reserve
                    if status in (400, 422) and operation == "judge" and OPTIONAL_JUDGE_FIELDS & payload.keys():
                        # Some compatible endpoints reject JSON mode or a temperature for
                        # reasoning models. Fall back to the plain Chat Completions request.
                        payload = {key: value for key, value in payload.items() if key not in OPTIONAL_JUDGE_FIELDS}
                        self.telemetry["judge_plain_fallbacks"] += 1
                        error = CallError(f"http_{status}", f"{operation} rejected optional fields (HTTP {status}).")
                        continue
                    if status not in (408, 429) and status < 500:
                        raise CallError(f"http_{status}", f"{operation} rejected the request (HTTP {status}).")
                    floor = 2 ** (attempt + 1) if status == 429 else 2**attempt
                    delay = max(floor, retry_delay(response.headers.get("retry-after", "").strip(), attempt, datetime.now(UTC)))
                    # Jitter spreads concurrent retries so they do not collide again.
                    delay += random.uniform(0, delay / 4)
                    if status == 429:
                        self.cooldown[key_name] = max(self.cooldown.get(key_name, 0), monotonic() + delay)
                    error = CallError(f"http_{status}", f"{operation} unavailable (HTTP {status}).")
            if delay > self.max_retry_wait:
                raise CallError(
                    "retry_delay",
                    f"{operation} asked to retry after {round(delay)} s, beyond the {self.max_retry_wait}-second wait limit (often a daily quota).",
                )
            if attempt + 1 < ATTEMPTS:
                self.telemetry["retries"] += 1
                await asyncio.sleep(delay)
        raise error

    async def translate(self, text: str, lang: str, *, source_lang: str = "en") -> Call:
        """Translate from the declared source language and cache complete output with its cost."""
        max_tokens = min(8192, max(128, len(text) * 2))
        # The hosted gateway pins decoding and accepts this provider-specific budget field.
        payload = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "source_language_code": source_lang,
            "target_language_code": lang,
            "max_output_tokens": max_tokens,
        }

        def decode(body: bytes) -> tuple[bytes, dict[str, Any]]:
            """Extract the translation and bill from provider usage when available."""
            data = json_object(body)
            try:
                choice = data["choices"][0]
                translation = choice["message"]["content"]
            except (KeyError, IndexError, TypeError) as error:
                raise CallError("invalid_response", "Indic-Translate returned no text.") from error
            if choice.get("finish_reason") == "length":
                raise CallError("truncated", "Indic-Translate hit its output token limit.")
            if not isinstance(translation, str) or not translation.strip():
                raise CallError("invalid_response", "Indic-Translate returned no text.")
            _, used = token_counts(data.get("usage"))
            return translation.encode(), {"micro_inr": 20 * (max_tokens if used is None else used), "output_tokens": used}

        return await self._call("translate", f"{BASE_URL}/v1/chat/completions", "BODHAN_TRANSLATE_KEY", payload, 20 * max_tokens, decode)

    async def speak(self, text: str, lang: str) -> Call:
        """Synthesize a short script in the configured voice and optional style for its language."""
        instructions = {"lang": lang, **({"style": self.speech_style} if self.speech_style else {})}
        payload = {
            "model": "indic-speak",
            "input": text,
            "voice": self.voices[lang],
            "instructions": json.dumps(instructions),
        }
        reserve = len(text) * 600

        def decode(body: bytes) -> tuple[bytes, dict[str, Any]]:
            """Accept hosted RIFF/WAVE bytes and record metered input characters."""
            if body[:4] != b"RIFF" or body[8:12] != b"WAVE":
                raise CallError("invalid_response", "Indic-Speak did not return WAV audio.")
            return body, {"micro_inr": reserve, "characters": len(text)}

        return await self._call("speak", f"{BASE_URL}/v1/audio/speech", "BODHAN_SPEAK_KEY", payload, reserve, decode)

    async def transcribe(self, wav: bytes, lang: str, duration_ms: int) -> Call:
        """Transcribe a bounded audio chunk with a known spoken language."""
        payload = {"model": "indic-transcribe", "language": lang}
        reserve = (duration_ms * 100 + 59) // 60

        def decode(body: bytes) -> tuple[bytes, dict[str, Any]]:
            """Accept a string transcript, including an empty transcript for silence."""
            transcript = json_object(body).get("text")
            if not isinstance(transcript, str):
                raise CallError("invalid_response", "Indic-Transcribe returned no transcript field.")
            return transcript.encode(), {"micro_inr": reserve, "audio_ms": duration_ms}

        return await self._call(
            "transcribe",
            f"{BASE_URL}/v1/audio/transcriptions",
            "BODHAN_TRANSCRIBE_KEY",
            payload,
            reserve,
            decode,
            wav=wav,
        )

    async def judge(
        self,
        rubric: str,
        context: str,
        *,
        validate_verdict: Callable[[bytes], None],
        fresh: bool = False,
    ) -> Call:
        """Call the configured judge and validate its verdict before caching the response."""
        if not self.judge_url.startswith(("http://", "https://")) or not self.judge_model:
            raise CallError("missing_configuration", "Set LLM_BASE_URL and LLM_MODEL for the judge.")
        payload = {
            "model": self.judge_model,
            "messages": [{"role": "system", "content": rubric}, {"role": "user", "content": context}],
            # Deterministic decoding and JSON mode where supported. Both are dropped on HTTP 400.
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        def decode(body: bytes) -> tuple[bytes, dict[str, Any]]:
            """Extract assistant text and usage without assuming a pricing contract."""
            response = json_object(body)
            sent, received = token_counts(response.get("usage"))
            # Malformed verdicts can still be billed. Retain live token usage
            # before rejecting the result and leaving the cache empty.
            self.telemetry["judge_input_tokens"] += sent or 0
            self.telemetry["judge_output_tokens"] += received or 0
            try:
                content = response["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as error:
                raise CallError("invalid_response", "Judge endpoint returned no assistant message.") from error
            if not isinstance(content, str) or not content.strip():
                raise CallError("invalid_response", "Judge endpoint returned empty assistant text.")
            value = content.encode()
            validate_verdict(value)
            return value, {"input_tokens": sent, "output_tokens": received}

        return await self._call(
            "judge",
            self.judge_url + "/chat/completions",
            "LLM_API_KEY",
            payload,
            0,
            decode,
            fresh=fresh,
        )
