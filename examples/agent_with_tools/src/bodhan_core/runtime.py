"""Process-wide runtime settings for Bodhan calls: base URL, the concurrency limit and the HTTP client."""

from __future__ import annotations

import asyncio
import os

import httpx

from bodhan_core.config import section

_HTTP = section("http")

# One shared limiter for every tool, because the API reports a single parallel-request limit.
API_SLOTS = asyncio.Semaphore(int(os.environ.get("BODHAN_CONCURRENCY", _HTTP["concurrency"])))
BACKOFF_S = float(_HTTP["backoff_s"])


def base_url() -> str:
    return os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")


def make_client(timeout_s: float | None = None, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """An AsyncClient with the configured per-call timeout. ``transport`` lets tests inject a mock."""
    return httpx.AsyncClient(timeout=timeout_s or float(_HTTP["timeout_s"]), transport=transport)
