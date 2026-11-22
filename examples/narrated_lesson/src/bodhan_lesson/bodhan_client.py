"""Small HTTP helper for Bodhan hosted APIs.

Handles auth headers, JSON errors, and retries on 429 / 5xx.
Honors Retry-After on rate limits (Bodhan sliding RPM window).
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import requests

DEFAULT_BASE_URL = "https://api.bodhan.ai"
DEFAULT_TIMEOUT_S = 180
# Enough attempts to ride out a short RPM window when Retry-After is short.
DEFAULT_MAX_RETRIES = 5
# Cap so a bad/missing header cannot stall a run indefinitely.
MAX_RETRY_AFTER_S = 120.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def get_base_url() -> str:
    """Return Bodhan API base URL (no trailing slash)."""
    return os.environ.get("BODHAN_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def get_ocr_api_key() -> str:
    """Return the OCR key. Prefer BODHAN_OCR_KEY; fall back to BODHAN_API_KEY."""
    key = os.environ.get("BODHAN_OCR_KEY") or os.environ.get("BODHAN_API_KEY")
    if not key:
        raise RuntimeError(
            "Missing OCR API key. Set BODHAN_OCR_KEY (or BODHAN_API_KEY) in .env"
        )
    return key


def get_translate_api_key() -> str:
    """Return the translate key. Prefer BODHAN_TRANSLATE_KEY; fall back to BODHAN_API_KEY."""
    key = os.environ.get("BODHAN_TRANSLATE_KEY") or os.environ.get("BODHAN_API_KEY")
    if not key:
        raise RuntimeError(
            "Missing translate API key. Set BODHAN_TRANSLATE_KEY (or BODHAN_API_KEY) in .env"
        )
    return key


def get_speak_api_key() -> str:
    """Return the speak key. Prefer BODHAN_SPEAK_KEY; fall back to BODHAN_API_KEY."""
    key = os.environ.get("BODHAN_SPEAK_KEY") or os.environ.get("BODHAN_API_KEY")
    if not key:
        raise RuntimeError(
            "Missing speak API key. Set BODHAN_SPEAK_KEY (or BODHAN_API_KEY) in .env"
        )
    return key


def get_transcribe_api_key() -> str:
    """Return the transcribe key. Prefer BODHAN_TRANSCRIBE_KEY; fall back to BODHAN_API_KEY."""
    key = os.environ.get("BODHAN_TRANSCRIBE_KEY") or os.environ.get("BODHAN_API_KEY")
    if not key:
        raise RuntimeError(
            "Missing transcribe API key. Set BODHAN_TRANSCRIBE_KEY (or BODHAN_API_KEY) in .env"
        )
    return key


def _error_payload(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
        err = payload.get("error") if isinstance(payload, dict) else None
        return err if isinstance(err, dict) else {}
    except (ValueError, AttributeError):
        return {}


def _format_bodhan_error(response: requests.Response) -> str:
    err = _error_payload(response)
    if err:
        code = err.get("code", response.status_code)
        message = err.get("message", response.text[:200])
        request_id = err.get("request_id", "")
        return f"{response.status_code} {code}: {message} (request_id={request_id})"
    return f"{response.status_code}: {response.text[:200]}"


def _is_credit_exhausted(response: requests.Response) -> bool:
    """True when 429 is budget/credit, not RPM — do not retry."""
    if response.status_code != 429:
        return False
    err = _error_payload(response)
    blob = f"{err.get('code', '')} {err.get('message', '')} {response.text[:300]}".lower()
    return bool(
        re.search(r"\b(credit|credits|budget|insufficient|out of (?:credit|funds|balance))\b", blob)
    )


def _retry_wait_s(response: requests.Response, attempt: int) -> float:
    """Seconds to sleep before the next attempt.

    Prefer Retry-After on 429 (Bodhan docs). Otherwise exponential backoff
    with a stronger floor for rate limits than for transient 5xx.
    """
    header = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if header:
        try:
            wait = float(str(header).strip())
            if wait >= 0:
                return min(wait, MAX_RETRY_AFTER_S)
        except ValueError:
            pass
    if response.status_code == 429:
        # 2, 4, 8, 16… capped — better than 1s/2s when the header is missing.
        return min(float(2 ** (attempt + 1)), MAX_RETRY_AFTER_S)
    return min(float(2**attempt), MAX_RETRY_AFTER_S)


def _should_retry(response: requests.Response, *, attempt: int, max_retries: int) -> bool:
    if attempt + 1 >= max_retries:
        return False
    if response.status_code not in RETRYABLE_STATUS:
        return False
    if _is_credit_exhausted(response):
        return False
    return True


def post_json(
    path: str,
    body: dict[str, Any],
    *,
    api_key: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """POST JSON to Bodhan and return the parsed body.

    Retries on 429 / 5xx using Retry-After when present.
    """
    url = f"{get_base_url()}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error = "request failed"
    for attempt in range(max_retries):
        response = requests.post(url, headers=headers, json=body, timeout=timeout_s)
        if response.ok:
            return response.json()

        last_error = _format_bodhan_error(response)
        if _should_retry(response, attempt=attempt, max_retries=max_retries):
            time.sleep(_retry_wait_s(response, attempt))
            continue
        raise RuntimeError(last_error)

    raise RuntimeError(last_error)


def post_bytes(
    path: str,
    body: dict[str, Any],
    *,
    api_key: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> bytes:
    """POST JSON to Bodhan and return raw response bytes (e.g. audio/wav)."""
    url = f"{get_base_url()}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error = "request failed"
    for attempt in range(max_retries):
        response = requests.post(url, headers=headers, json=body, timeout=timeout_s)
        if response.ok:
            return response.content

        last_error = _format_bodhan_error(response)
        if _should_retry(response, attempt=attempt, max_retries=max_retries):
            time.sleep(_retry_wait_s(response, attempt))
            continue
        raise RuntimeError(last_error)

    raise RuntimeError(last_error)


def post_multipart(
    path: str,
    *,
    api_key: str,
    files: dict[str, Any],
    data: dict[str, Any],
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """POST multipart form data (e.g. audio transcriptions) and return JSON."""
    url = f"{get_base_url()}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}

    last_error = "request failed"
    for attempt in range(max_retries):
        # Re-open file handles each attempt when values are callables.
        prepared_files = {}
        for key, value in files.items():
            if callable(value):
                prepared_files[key] = value()
            else:
                prepared_files[key] = value
        response = requests.post(
            url,
            headers=headers,
            files=prepared_files,
            data=data,
            timeout=timeout_s,
        )
        if response.ok:
            return response.json()

        last_error = _format_bodhan_error(response)
        if _should_retry(response, attempt=attempt, max_retries=max_retries):
            time.sleep(_retry_wait_s(response, attempt))
            continue
        raise RuntimeError(last_error)

    raise RuntimeError(last_error)
