"""Async client for the Bodhan endpoints: per-model keys, retries with backoff, a concurrency limit and a disk cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
from pathlib import Path

import httpx2 as httpx

KEY_ENV = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}
ROUTES = {
    "indic-transcribe": "/v1/audio/transcriptions",
    # The documented POST /translate returns 404 on the live API (2026-11-21); the documented chat transport works.
    "indic-translate": "/v1/chat/completions",
    "indic-speak": "/v1/audio/speech",
    "indic-ocr": "/v1/chat/completions",
}

# Hosted prices from the assignment brief, in rupees.
INR_PER_AUDIO_MIN = 0.10
INR_PER_OUT_TOKEN = 0.20 / 10_000
INR_PER_TTS_CHAR = 6.0 / 10_000
INR_PER_OCR_IMAGE = 0.20

ATTEMPTS = 3
RATE_LIMIT_ATTEMPTS = 6  # 429s clear within a window; back off up to about a minute in total


class ToolFailure(Exception):
    """A failure the caller can act on. `code` is stable; `hint` says what to change."""

    def __init__(self, code: str, message: str, *, hint: str | None = None, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.code, self.hint, self.retryable, self.status = code, hint, retryable, status

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "hint": self.hint, "retryable": self.retryable}


_semaphores: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    loop = id(asyncio.get_running_loop())
    if loop not in _semaphores:
        _semaphores[loop] = asyncio.Semaphore(int(os.environ.get("BODHAN_MAX_PARALLEL", "4")))
    return _semaphores[loop]


def cache_dir() -> Path:
    return Path(os.environ.get("BODHAN_CACHE_DIR") or Path.home() / ".cache" / "bodhan_mcp").expanduser()


async def _post(url: str, headers: dict, timeout: float, **kw) -> tuple[int, httpx.Headers, bytes]:
    """The only network call. Tests replace it."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, **kw)
        return resp.status_code, resp.headers, resp.content


def _error(model: str, status: int, headers, body: bytes) -> ToolFailure:
    try:
        payload = json.loads(body)
        err = payload.get("error") or {}
        code = str(err.get("code") or payload.get("detail") or status) if isinstance(err, dict) else str(err)
        message = (err.get("message") if isinstance(err, dict) else None) or str(payload.get("detail") or body[:300])
    except (ValueError, AttributeError):
        code, message = str(status), body[:300].decode("utf-8", "replace")
    message = f"{model} returned HTTP {status}: {message}"
    if status == 401:
        return ToolFailure("invalid_api_key", message, hint=f"Check {KEY_ENV[model]}.", status=status)
    if status == 429:
        return ToolFailure(
            "rate_limited",
            message,
            hint="Rate limit or monthly budget reached. Retry the same call later: finished pieces are cached and cost nothing again.",
            retryable=True,
            status=status,
        )
    if status == 413:
        return ToolFailure("payload_too_large", message, hint="Send a shorter input.", status=status)
    if status == 415:
        return ToolFailure("unsupported_media", message, hint="Send WAV, FLAC, OGG or MP3 audio.", status=status)
    # OCR 502 means a block exceeded max_tokens or the image did not decode; the caller decides, so it is not retried here.
    if status >= 500 and not (model == "indic-ocr" and status == 502):
        return ToolFailure("upstream_error", message, hint="Bodhan had a server error; retry later.", retryable=True, status=status)
    return ToolFailure(code, message, status=status)


async def call(
    model: str, *, json_body: dict | None = None, form: dict | None = None, file: tuple[str, bytes, str] | None = None, fresh: bool = False
) -> tuple[bytes, bool]:
    """POST one request to `model`. Returns (raw body, served_from_cache).

    Identical requests are answered from the disk cache, so a run never pays twice for the same input.
    `fresh=True` skips the cache read (used only when a verified result was bad and we retry).
    """
    digest = hashlib.sha256(json.dumps([model, json_body, form], sort_keys=True, ensure_ascii=False).encode())
    if file:
        digest.update(file[1])
    cached = cache_dir() / f"{digest.hexdigest()}.bin"
    if not fresh and cached.exists():
        return cached.read_bytes(), True

    key = os.environ.get(KEY_ENV[model]) or os.environ.get("BODHAN_API_KEY")
    if not key:
        raise ToolFailure("missing_api_key", f"{KEY_ENV[model]} is not set.", hint="Add it to .env; see .env.example.")
    base = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")
    timeout = float(os.environ.get("BODHAN_TIMEOUT_S", "90"))
    kw = {"json": json_body} if json_body is not None else {"data": form, "files": {"file": file}}

    err: ToolFailure | None = None
    attempt = 0
    while True:
        retry_after = 0.0
        try:
            async with _semaphore():
                status, headers, body = await _post(base + ROUTES[model], {"Authorization": f"Bearer {key}"}, timeout, **kw)
        except httpx.TimeoutException:
            err = ToolFailure("timeout", f"{model} did not answer within {timeout:.0f}s.", hint="Retry later or send a smaller input.", retryable=True)
        except httpx.TransportError as exc:
            err = ToolFailure("network_error", f"Could not reach {base}: {exc}", hint="Check the network.", retryable=True)
        else:
            if status < 400:
                cached.parent.mkdir(parents=True, exist_ok=True)
                tmp = cached.with_suffix(".tmp")
                tmp.write_bytes(body)
                tmp.replace(cached)
                return body, False
            err = _error(model, status, headers, body)
            if not err.retryable:
                raise err
            try:
                retry_after = float(headers.get("retry-after") or 0)
            except ValueError:
                retry_after = 0.0
        attempt += 1
        if attempt >= (RATE_LIMIT_ATTEMPTS if err.code == "rate_limited" else ATTEMPTS):
            raise err
        await asyncio.sleep(min(max(retry_after, 2**attempt + random.random()), 30))
