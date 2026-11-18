"""A small OpenAI-compatible chat client with tool calling.

It speaks the standard ``POST {LLM_BASE_URL}/chat/completions`` protocol over httpx, so any OpenAI-compatible
provider works. Retries 429, 5xx and network errors with backoff (honouring Retry-After), because one busy
moment at the provider should not throw away a run's paid model work. Each request has a timeout.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_WAIT_S = 30.0


class LLMError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _configured(value: str | None) -> bool:
    return bool(value) and not value.strip().startswith("<")


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str | None,
        model: str | None,
        api_key: str | None,
        timeout_s: float = 120.0,
        max_attempts: int = 6,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not (_configured(base_url) and _configured(model) and _configured(api_key)):
            raise LLMError("missing_config", "Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY (any OpenAI-compatible endpoint with tool calling).")
        self.model = model
        self.max_attempts = max_attempts
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._sleep = sleep
        self._send_temperature = True
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @classmethod
    def from_env(cls, env: dict[str, str], **kwargs: Any) -> LLMClient:
        return cls(base_url=env.get("LLM_BASE_URL"), model=env.get("LLM_MODEL"), api_key=env.get("LLM_API_KEY"), **kwargs)

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """One chat turn. Returns the assistant message as a dict (``content`` and optional ``tool_calls``)."""
        last: LLMError | None = None
        for attempt in range(1, self.max_attempts + 1):
            payload: dict[str, Any] = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": "auto"}
            if self._send_temperature:
                payload["temperature"] = 0
            try:
                resp = self._http.post("/chat/completions", json=payload, headers=self._headers)
            except httpx.TransportError as exc:
                last = LLMError("network_error", f"Could not reach the LLM ({exc.__class__.__name__}).")
                resp = None
            else:
                if resp.status_code == 200:
                    return self._parse(resp)
                text = resp.text[:300]
                # Some models reject temperature; drop it once and retry straight away.
                if resp.status_code == 400 and "temperature" in text.lower() and self._send_temperature:
                    self._send_temperature = False
                    continue
                last = LLMError(f"http_{resp.status_code}", text)
                if resp.status_code not in RETRY_STATUS:
                    raise last
            if attempt < self.max_attempts:
                self._sleep(self._wait_s(attempt, resp))
        assert last is not None
        raise last

    @staticmethod
    def _wait_s(attempt: int, resp: httpx.Response | None) -> float:
        """Retry-After when the provider sends it, otherwise 2, 4, 8, 16, 30 s with a little jitter."""
        if resp is not None:
            try:
                return min(MAX_WAIT_S, max(0.0, float(resp.headers.get("retry-after", ""))))
            except ValueError:
                pass
        return min(MAX_WAIT_S, 2 ** attempt) + random.uniform(0, 0.5)

    def _parse(self, resp: httpx.Response) -> dict:
        try:
            body = resp.json()
            message = body["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("bad_response", "The LLM response did not have the chat-completions shape.") from exc
        usage = body.get("usage") or {}
        self.calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
        }
