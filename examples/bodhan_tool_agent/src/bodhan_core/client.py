"""The ONLY module that talks to Bodhan. Retries with backoff, concurrency limit, per-call timeout."""

from __future__ import annotations

import random
import threading
import time
from typing import Any

import httpx

from .config import KEY_ENV, Settings
from .errors import ToolError


def _error_from_response(model: str, resp: httpx.Response) -> ToolError:
    msg, code, rid = resp.text[:300], "", ""
    try:
        err = resp.json().get("error", {})
        msg, code, rid = err.get("message", msg), err.get("code", ""), err.get("request_id", "")
    except (ValueError, AttributeError):
        pass
    s = resp.status_code
    detail = {"status": s, "api_code": code, "request_id": rid, "model": model}
    if s in (401, 403):
        return ToolError("auth_failed", f"{model} rejected the API key: {msg}", f"Check {KEY_ENV[model]}.", **detail)
    if s == 429:
        return ToolError("rate_limited", f"{model} rate limit or quota hit: {msg}", "Wait and retry later; do not loop.", True, **detail)
    if 400 <= s < 500:
        return ToolError("bad_request", f"{model} rejected the request: {msg}", "Fix the input (format, length, language).", **detail)
    return ToolError("upstream_error", f"{model} server error {s}: {msg}", "Retry later.", True, **detail)


class BodhanClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._sem = threading.BoundedSemaphore(settings.max_concurrency)
        self._http = httpx.Client(base_url=settings.base_url, transport=transport, timeout=settings.timeout_s)
        self._sleep = time.sleep  # overridable in tests

    def _key(self, model: str) -> str:
        key = self.settings.keys.get(model, "")
        if not key:
            raise ToolError("missing_api_key", f"No API key configured for {model}.", f"Set {KEY_ENV[model]} (or BODHAN_API_KEY).")
        return key

    def request(self, model: str, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._key(model)}"}
        attempts = self.settings.max_retries + 1
        last: ToolError | None = None
        for attempt in range(attempts):
            try:
                with self._sem:
                    resp = self._http.request(method, path, headers=headers, **kw)
            except httpx.TimeoutException:
                last = ToolError("timeout", f"{model} call timed out after {self.settings.timeout_s:.0f}s.", "Retry or use a shorter input.", True)
            except httpx.TransportError as e:
                last = ToolError("network_error", f"Network error calling {model}: {e}", "Check connectivity.", True)
            else:
                if resp.is_success:
                    return resp
                last = _error_from_response(model, resp)
                # 429 gets a single patient retry only; other 4xx never retry.
                if not last.retryable or (resp.status_code == 429 and attempt >= 1):
                    raise last
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    self._sleep(min(int(retry_after), 30))
                    continue
            if attempt < attempts - 1:
                self._sleep(min(0.5 * 2**attempt, 8) + random.random() * 0.25)
        assert last is not None
        raise last

    def post_json(self, model: str, path: str, body: dict[str, Any]) -> httpx.Response:
        return self.request(model, "POST", path, json=body)

    def post_multipart(self, model: str, path: str, files: dict[str, Any], data: dict[str, str]) -> httpx.Response:
        return self.request(model, "POST", path, files=files, data=data)
