"""The single HTTP path to api.bodhan.ai: auth routing, retries, timeouts, concurrency cap."""
from __future__ import annotations

import random
import threading
import time
from typing import Any

import httpx

from . import config
from .errors import BodhanError

_transport: httpx.BaseTransport | None = None  # tests inject httpx.MockTransport
_sleep = time.sleep
_sem_lock = threading.Lock()
_sem: threading.BoundedSemaphore | None = None
MAX_ATTEMPTS = 3


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport, _sem
    _transport = transport
    _sem = None


def _semaphore() -> threading.BoundedSemaphore:
    global _sem
    with _sem_lock:
        if _sem is None:
            _sem = threading.BoundedSemaphore(config.max_concurrency())
        return _sem


def _api_error(resp: httpx.Response) -> tuple[str, str]:
    """Pull (code, message) from the several envelope shapes seen live; never raises."""
    try:
        body = resp.json()
        if isinstance(body, list) and body:
            body = body[0]
        err = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(err, str):
            return "", err[:200]
        return str(err.get("code") or err.get("type") or ""), str(err.get("message") or resp.text[:200])
    except (ValueError, TypeError, AttributeError):
        return "", resp.text[:200]


def _raise_for(resp: httpx.Response, kind: str) -> BodhanError:
    code, msg = _api_error(resp)
    rid = resp.headers.get("x-request-id")
    s = resp.status_code
    model = config.MODELS[kind]
    if s in (401, 403):
        return BodhanError("AUTH_FAILED", f"{model} rejected the API key.", hint="Check the key for this model in the Bodhan console.", request_id=rid, status=s)
    if s == 413:
        return BodhanError("PAYLOAD_TOO_LARGE", f"{model} refused the input as too large.", request_id=rid, status=s)
    if s == 415:
        return BodhanError("UNSUPPORTED_FORMAT", f"{model} refused the media type: {msg}", hint="Use WAV, FLAC, OGG or MP3.", request_id=rid, status=s)
    if s == 429:
        if "retry-after" not in resp.headers and "credit" in (code + msg).lower():
            return BodhanError("BUDGET_EXCEEDED", f"{model}: account credit is exhausted.", hint="Top up credit in the Bodhan console.", request_id=rid, status=s)
        return BodhanError("RATE_LIMITED", f"{model} rate limit reached.", retryable=True, hint="Retry later or lower BODHAN_MAX_CONCURRENCY.", request_id=rid, status=s)
    if s >= 500:
        return BodhanError("UPSTREAM_ERROR", f"{model} failed ({s} {code}): {msg}", retryable=True, request_id=rid, status=s)
    return BodhanError("BAD_REQUEST", f"{model} rejected the request ({s} {code}): {msg}", request_id=rid, status=s)


def _error_in_200(resp: httpx.Response) -> bool:
    if "json" not in resp.headers.get("content-type", ""):
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    return isinstance(body, dict) and "error" in body and "choices" not in body and "text" not in body


def post(
    kind: str,
    path: str,
    *,
    json: Any | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    retry_5xx: bool = True,
) -> httpx.Response:
    """POST with the right key and bounded concurrency.

    Retries 429/5xx (3 attempts, backoff, Retry-After honoured) and one auth blip.
    A timeout is NOT retried: the provider may already have billed the request.
    """
    headers = {"Authorization": f"Bearer {config.api_key(kind)}"}
    last: BodhanError | None = None
    auth_retried = False
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        attempt += 1
        resp: httpx.Response | None = None
        try:
            with _semaphore(), httpx.Client(transport=_transport, timeout=config.timeout_s(kind)) as c:
                resp = c.post(config.base_url() + path, headers=headers, json=json, data=data, files=files)
        except httpx.TimeoutException:
            raise BodhanError(
                "TIMEOUT",
                f"{config.MODELS[kind]} did not answer within {config.timeout_s(kind):.0f}s; not retried because it may already be billed.",
                retryable=True,
                hint="Retry with a smaller input or a higher BODHAN_TIMEOUT_S.",
            ) from None
        except httpx.HTTPError as exc:
            last = BodhanError("UPSTREAM_ERROR", f"Network error calling {config.MODELS[kind]}: {type(exc).__name__}", retryable=True)
        if resp is not None:
            if resp.status_code < 400 and not _error_in_200(resp):
                return resp
            if resp.status_code < 400:
                code, msg = _api_error(resp)
                raise BodhanError("UPSTREAM_ERROR", f"{config.MODELS[kind]} returned an error body: {code} {msg}", retryable=False)
            last = _raise_for(resp, kind)
            if last.code == "AUTH_FAILED" and not auth_retried:
                auth_retried = True  # intermittent 401/403 seen on keys that had just worked
                attempt -= 1
                _sleep(1.0)
                continue
            if not last.retryable or (resp.status_code >= 500 and not retry_5xx):
                raise last
        if attempt >= MAX_ATTEMPTS:
            break
        wait = 2.0 ** (attempt - 1) + random.random() * 0.5
        if resp is not None and resp.status_code == 429:
            try:
                wait = max(wait, min(float(resp.headers.get("retry-after", 0)), 30.0))
            except ValueError:
                pass
        _sleep(wait)
    assert last is not None
    raise last
