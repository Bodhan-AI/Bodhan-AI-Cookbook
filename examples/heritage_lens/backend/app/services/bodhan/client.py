"""Single HTTPX gateway to https://api.bodhan.ai with retries, timeouts and safe errors."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Iterable

import httpx

from app.core.logging import redact, register_secret
from app.services.bodhan.exceptions import (
    BodhanError, BodhanNetworkError, BodhanNotConfigured, BodhanResponse, BodhanTimeout,
)

logger = logging.getLogger("heritagelens.bodhan")
DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# 400/401/413/415/422 are never retried: repeating them cannot succeed.


def _parse_error(resp: httpx.Response) -> tuple[str, str | None, str | None, str | None]:
    """Return (message, code, request_id, detail) from either documented error envelope."""
    request_id = resp.headers.get("x-request-id")
    message, code, detail = f"HTTP {resp.status_code}", None, None
    try:
        body = resp.json()
    except Exception:
        text = redact(resp.text[:300]) if resp.content else ""
        return (text or message), None, request_id, None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):  # {"error": {"message", "code", "request_id" | "type"}}
            message = str(err.get("message") or message)
            code = str(err.get("code")) if err.get("code") is not None else None
            request_id = request_id or err.get("request_id")
        elif isinstance(err, str):
            message = err
        d = body.get("detail")
        if d is not None:  # FastAPI-style {"detail": ...}, also used by OCR 502
            detail = d if isinstance(d, str) else str(d)
            if message.startswith("HTTP "):
                message = detail
    return redact(message[:500]), code, request_id, redact(detail[:500]) if detail else None


class BodhanClient:
    def __init__(
        self,
        *,
        base_url: str,
        key_resolver: Callable[[str], str | None],
        timeouts: dict[str, float] | None = None,
        connect_timeout: float = 10.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._key_resolver = key_resolver
        self._timeouts = timeouts or {}
        self._connect_timeout = connect_timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), transport=transport,
                                       headers={"User-Agent": "HeritageLens/0.1"})

    @classmethod
    def from_settings(cls, settings, **overrides) -> "BodhanClient":
        from app.core.config import SERVICES

        return cls(
            base_url=settings.bodhan_base_url,
            key_resolver=settings.key_for,
            timeouts={s: settings.timeout_for(s) for s in SERVICES},
            connect_timeout=settings.bodhan_connect_timeout,
            max_retries=settings.bodhan_max_retries,
            backoff_base=settings.bodhan_backoff_base_seconds,
            **overrides,
        )

    def is_configured(self, service: str) -> bool:
        return bool(self._key_resolver(service))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        service: str,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        retry_statuses: Iterable[int] = DEFAULT_RETRY_STATUSES,
        max_retries: int | None = None,
    ) -> BodhanResponse:
        key = self._key_resolver(service)
        if not key:
            raise BodhanNotConfigured(service, "No Bodhan API key is configured for this service.")
        register_secret(key)
        retry_set = frozenset(retry_statuses)
        attempts = 1 + (self._max_retries if max_retries is None else max_retries)
        timeout = httpx.Timeout(self._timeouts.get(service, 60.0), connect=self._connect_timeout)
        headers = {"Authorization": f"Bearer {key}"}
        last_exc: BodhanError | None = None

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                resp = await self._http.request(method, path, json=json_body, data=data, files=files,
                                                headers=headers, timeout=timeout)
            except httpx.ConnectTimeout:
                last_exc = BodhanTimeout(service, "Connection timed out.")
                logger.warning("bodhan %s %s connect_timeout attempt=%d/%d", service, path, attempt, attempts)
            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
                last_exc = BodhanTimeout(service, "The request timed out while waiting for a response.")
                logger.warning("bodhan %s %s timeout attempt=%d/%d", service, path, attempt, attempts)
            except httpx.TimeoutException:
                last_exc = BodhanTimeout(service, "The request timed out.")
                logger.warning("bodhan %s %s timeout attempt=%d/%d", service, path, attempt, attempts)
            except httpx.ConnectError as exc:
                last_exc = BodhanNetworkError(service, f"Could not connect to the service: {exc}")
                logger.warning("bodhan %s %s connect_error attempt=%d/%d: %s", service, path, attempt, attempts, exc)
            except httpx.TransportError as exc:
                last_exc = BodhanNetworkError(service, f"Could not connect to the service: {exc}")
                logger.warning("bodhan %s %s network_error attempt=%d/%d: %s", service, path, attempt, attempts, exc)
            else:
                elapsed = time.monotonic() - started
                logger.info("bodhan %s %s status=%d attempt=%d/%d %.2fs", service, path,
                            resp.status_code, attempt, attempts, elapsed)
                if 200 <= resp.status_code < 300:
                    parsed = None
                    if "json" in resp.headers.get("content-type", "").lower():
                        try:
                            parsed = resp.json()
                        except Exception:
                            parsed = None
                    return BodhanResponse(resp.status_code, {k.lower(): v for k, v in resp.headers.items()},
                                          resp.content, parsed, resp.headers.get("x-request-id"))
                message, code, request_id, detail = _parse_error(resp)
                last_exc = BodhanError(service, message, status_code=resp.status_code, code=code,
                                       request_id=request_id, detail=detail)
                if resp.status_code not in retry_set:
                    raise last_exc
                retry_after = resp.headers.get("retry-after", "")
                if attempt < attempts and retry_after.isdigit():
                    await self._sleep(min(float(retry_after), 10.0))
                    continue
            if attempt < attempts:
                await self._sleep(self._backoff_base * (2 ** (attempt - 1)))
        assert last_exc is not None
        raise last_exc
