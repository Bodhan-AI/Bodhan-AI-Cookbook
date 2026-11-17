"""One API boundary: reservations, total-call timeout and private replay cache."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Callable

import httpx

from .runtime import Run, ToolError, digest


class Gateway:
    def __init__(self, run: Run, transport: httpx.AsyncBaseTransport | None = None):
        self.run = run
        self.client = httpx.AsyncClient(transport=transport, follow_redirects=False)
        self.last_planner_dispatch = 0.0
        self.lock = asyncio.Lock()  # concurrency limit = 1, including reservations
        self.cache_dir = run.output.parent / ".bodhan-cache"
        self.cache_dir.mkdir(mode=0o700, exist_ok=True)
        self.cache_dir.chmod(0o700)

    async def close(self):
        await self.client.aclose()

    async def _send(self, url, key, payload, file, timeout):
        headers = {"Authorization": f"Bearer {key}"}
        if file:
            with file.open("rb") as stream:
                return await self.client.post(
                    url,
                    headers=headers,
                    data=payload,
                    files={"file": (file.name, stream, "audio/wav")},
                    timeout=timeout,
                )
        return await self.client.post(url, headers=headers, json=payload, timeout=timeout)

    async def request(
        self,
        *,
        model: str,
        url: str,
        key_env: str,
        payload: dict,
        reserve: Decimal,
        file: Path | None = None,
        actual_cost: Callable[[dict], Decimal] | None = None,
        binary: bool = False,
    ) -> dict:
        key = os.environ.get(key_env)
        if not key:
            raise ToolError("missing_credentials", f"Set {key_env} securely before live calls.")
        fingerprint = digest(
            {
                "cache_version": 1,
                "model": model,
                "url": url,
                "payload": payload,
                "file": digest(file.read_bytes()) if file else None,
            }
        )
        entry = self.cache_dir / (fingerprint + ".json")
        pending = self.cache_dir / (fingerprint + ".pending")
        async with self.lock:
            if fingerprint in self.run.cache:
                self.run.event("cache_hit", model=model, fingerprint=fingerprint, persistent=False)
                return self.run.cache[fingerprint]
            if entry.exists():
                try:
                    cached = json.loads(entry.read_text())
                    body = (
                        {"bytes": base64.b64decode(cached["base64"])} if binary else cached["body"]
                    )
                except (OSError, ValueError, KeyError):
                    raise ToolError(
                        "invalid_cache",
                        "Private cache entry is invalid. Inspect it before deciding whether a paid retry is safe.",
                    ) from None
                self.run.cache[fingerprint] = body
                self.run.event("cache_hit", model=model, fingerprint=fingerprint, persistent=True)
                return body
            if os.environ.get("BODHAN_CACHE_ONLY") == "1" and not model.startswith("planner:"):
                raise ToolError(
                    "cache_only_miss",
                    "Bodhan cache-only mode: no network call dispatched for this request.",
                )
            if fingerprint in self.run.uncertain or pending.exists():
                raise ToolError(
                    "uncertain_previous_call",
                    "A matching call is in progress or may have been billed; not re-sent.",
                )
            for attempt in range(3):
                if model.startswith("planner:"):
                    interval = float(os.environ.get("LLM_MIN_INTERVAL_SECONDS", "0"))
                    if not math.isfinite(interval) or not 0 <= interval <= 60:
                        raise ToolError(
                            "planner_config",
                            "Planner interval must be finite and between 0 and 60 seconds.",
                        )
                    delay = max(0, self.last_planner_dispatch + interval - time.monotonic())
                    if delay >= self.run.remaining_time():
                        raise ToolError("time_budget", "Planner pacing exceeds remaining run time.")
                    if delay:
                        self.run.event("planner_pacing", seconds=round(delay, 3))
                        await asyncio.sleep(delay)
                    self.last_planner_dispatch = time.monotonic()
                self.run.check_window(self.run.limits.call_timeout + 2)
                index = self.run.reserve(reserve, model)
                # Exclusive creation prevents two processes from dispatching the same input.
                try:
                    with pending.open("x") as stream:
                        stream.write(json.dumps({"model": model, "reserved_inr": str(reserve)}))
                    pending.chmod(0o600)
                except FileExistsError:
                    self.run.settle(index, Decimal(0), "not_dispatched_cache_lock")
                    raise ToolError(
                        "uncertain_previous_call",
                        "Matching request is already pending; not re-sent.",
                    ) from None
                started = time.monotonic()
                self.run.uncertain.add(fingerprint)
                try:
                    timeout = min(self.run.limits.call_timeout, self.run.remaining_time())
                    response = await asyncio.wait_for(
                        self._send(url, key, payload, file, timeout), timeout
                    )
                except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                    self.run.settle(index, None, "billing_uncertain")
                    raise ToolError(
                        "network_failure",
                        "Request failed or timed out; reserved cost retained, no blind retry.",
                    ) from exc
                self.run.event(
                    "api",
                    model=model,
                    fingerprint=fingerprint,
                    latency_seconds=round(time.monotonic() - started, 3),
                    http_status=response.status_code,
                    retry_after=response.headers.get("retry-after"),
                    request_id=response.headers.get("x-request-id"),
                )
                if not response.is_success:
                    certain_refusal = response.status_code in (
                        400,
                        401,
                        403,
                        404,
                        413,
                        415,
                        422,
                        429,
                    )
                    self.run.settle(
                        index,
                        Decimal(0) if certain_refusal else None,
                        "rejected" if certain_refusal else "billing_uncertain",
                    )
                    if certain_refusal:
                        self.run.uncertain.discard(fingerprint)
                        pending.unlink(missing_ok=True)
                    # Credit exhaustion is also 429. Retry only a marked rate-limit refusal.
                    if (
                        response.status_code == 429
                        and "retry-after" in response.headers
                        and attempt < 2
                    ):
                        try:
                            delay = float(response.headers["retry-after"])
                        except ValueError:
                            delay = float(2**attempt)
                        if not math.isfinite(delay) or delay < 0:
                            delay = float(2**attempt)
                        # Never retry sooner than Retry-After; stop if waiting cannot fit.
                        if delay >= self.run.remaining_time():
                            raise ToolError(
                                "time_budget", "Rate-limit backoff exceeds remaining run time."
                            )
                        await asyncio.sleep(max(delay, float(2**attempt)))
                        continue
                    try:
                        error_body = response.json()
                        if (
                            isinstance(error_body, list)
                            and error_body
                            and isinstance(error_body[0], dict)
                        ):
                            error_body = error_body[0]
                        detail = error_body.get("error", error_body.get("detail", {}))
                        reason = (
                            detail.get("message", detail.get("code", ""))
                            if isinstance(detail, dict)
                            else str(detail)
                        )
                        reason = str(reason).replace(key, "[redacted]")[:500]
                    except (ValueError, TypeError, AttributeError):
                        reason = "No structured error detail."
                    self.run.event(
                        "api_rejection",
                        model=model,
                        http_status=response.status_code,
                        detail=reason,
                        retry_after=response.headers.get("retry-after"),
                    )
                    raise ToolError(
                        "authentication_rejected"
                        if response.status_code in (401, 403)
                        else "api_rejected",
                        f"{model} returned HTTP {response.status_code}: {reason}",
                    )
                try:
                    body = {"bytes": response.content} if binary else response.json()
                    if not isinstance(body, dict) or "error" in body:
                        raise ValueError("Invalid response envelope")
                    actual = actual_cost(body) if actual_cost else reserve
                    self.run.settle(index, actual, "success")
                except (ValueError, KeyError, TypeError) as exc:
                    self.run.settle(index, None, "invalid_response_billing_uncertain")
                    raise ToolError(
                        "invalid_api_response",
                        f"{model} returned an invalid response; no duplicate retry.",
                    ) from exc
                stored = (
                    {"base64": base64.b64encode(body["bytes"]).decode()}
                    if binary
                    else {"body": body}
                )
                temporary = entry.with_suffix(".tmp")
                temporary.write_text(json.dumps(stored, ensure_ascii=False))
                temporary.chmod(0o600)
                temporary.replace(entry)
                pending.unlink(missing_ok=True)
                self.run.cache[fingerprint] = body
                self.run.uncertain.discard(fingerprint)
                return body
            raise ToolError("rate_limited", "API rate limit remained after bounded backoff.")
