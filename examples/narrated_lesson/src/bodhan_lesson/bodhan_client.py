"""Small HTTP helper for Bodhan hosted APIs.

Handles auth headers, JSON errors, and simple retries on 429 / 5xx.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

DEFAULT_BASE_URL = "https://api.bodhan.ai"
DEFAULT_TIMEOUT_S = 180
DEFAULT_MAX_RETRIES = 3


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


def _format_bodhan_error(response: requests.Response) -> str:
    try:
        err = response.json().get("error", {})
        code = err.get("code", response.status_code)
        message = err.get("message", response.text[:200])
        request_id = err.get("request_id", "")
        return f"{response.status_code} {code}: {message} (request_id={request_id})"
    except (ValueError, AttributeError):
        return f"{response.status_code}: {response.text[:200]}"


def post_json(
    path: str,
    body: dict[str, Any],
    *,
    api_key: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """POST JSON to Bodhan and return the parsed body.

    Retries on 429 and 5xx with simple exponential backoff.
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
        if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < max_retries:
            time.sleep(2**attempt)
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
        if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < max_retries:
            time.sleep(2**attempt)
            continue
        raise RuntimeError(last_error)

    raise RuntimeError(last_error)
