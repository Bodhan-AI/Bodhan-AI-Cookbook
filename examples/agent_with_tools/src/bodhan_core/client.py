"""POST to Bodhan with retries: timeouts, HTTP 429 and 5xx are retried with exponential backoff."""

from __future__ import annotations

import asyncio

import httpx

from bodhan_core.config import section
from bodhan_core.errors import ToolError
from bodhan_core.runtime import API_SLOTS, BACKOFF_S, base_url

MAX_ATTEMPTS = section("http")["max_attempts"]
MAX_RETRY_AFTER_S = 30.0


def retry_delay(resp: httpx.Response | None, attempt: int, backoff_s: float) -> float:
    """Seconds to wait before retry number ``attempt``: exponential backoff, or the server's Retry-After if it asks for longer."""
    delay = backoff_s * 2 ** (attempt - 1)
    try:
        asked = float(resp.headers.get("Retry-After", "")) if resp is not None else 0.0
    except ValueError:  # an HTTP-date form: ignore, backoff is enough
        asked = 0.0
    return min(max(delay, asked), max(MAX_RETRY_AFTER_S, delay))


async def post(client: httpx.AsyncClient, path: str, key: str, backoff_s: float = BACKOFF_S, **kwargs) -> httpx.Response | ToolError:
    """Returns the response on HTTP 200, otherwise a ToolError built from the API's error body."""
    url = f"{base_url()}{path}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with API_SLOTS:
                resp = await client.post(url, headers={"Authorization": f"Bearer {key}"}, **kwargs)
        except httpx.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                return ToolError(code="network_error", retryable=True, message=f"Could not reach Bodhan after {attempt} attempts: {exc!r}")
            await asyncio.sleep(backoff_s * 2 ** (attempt - 1))
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_ATTEMPTS:
                return ToolError(code="upstream_unavailable", retryable=True, message=f"Bodhan returned HTTP {resp.status_code} after {attempt} attempts.")
            await asyncio.sleep(retry_delay(resp, attempt, backoff_s))
            continue
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            return ToolError(code=err.get("code", f"http_{resp.status_code}"), message=err.get("message", resp.text[:300]))
        return resp
    return ToolError(code="unknown", message="Unexpected state.")  # pragma: no cover
