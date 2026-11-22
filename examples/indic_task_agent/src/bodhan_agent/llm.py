"""A small client for any OpenAI-compatible chat endpoint that supports tool calling.

We use plain httpx instead of a vendor SDK: fewer dependencies, no provider-specific
features, and the HTTP layer is easy to fake in tests.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

RETRYABLE = {429, 500, 502, 503, 504}


class LLMError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.retryable = code, message, status, retryable

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw: str = ""
    parse_error: str | None = None  # set when the model produced invalid JSON arguments


@dataclass
class LLMReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    reasoning: str | None = None  # some providers return their reasoning separately; logged, never echoed back

    def assistant_message(self) -> dict[str, Any]:
        """The message to put back into the history. Only standard fields are sent back,
        because providers add extra fields (e.g. reasoning) that they reject on input."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": "{}" if c.parse_error else (c.raw or json.dumps(c.arguments))}}
                for c in self.tool_calls
            ]
        return msg


def simplify_schema(schema: Any) -> Any:
    """Make a JSON schema friendlier to LLM providers.

    * anyOf [X, null]  ->  X   (an optional parameter is just one that is not in `required`)
    * drop 'title' and null defaults, which only add noise
    """
    if isinstance(schema, list):
        return [simplify_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    s = dict(schema)
    options = [o for o in s.get("anyOf", []) if o.get("type") != "null"]
    if "anyOf" in s and len(options) == 1:
        s.pop("anyOf")
        s = {**simplify_schema(options[0]), **s}
    s.pop("title", None)
    if "default" in s and s["default"] is None:
        del s["default"]
    if "properties" in s:
        s["properties"] = {name: simplify_schema(sub) for name, sub in s["properties"].items()}
    if "items" in s:
        s["items"] = simplify_schema(s["items"])
    return s


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str, *, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_attempts: int = 4, timeout: float = 90.0) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key = model, api_key
        self.http = http or httpx.Client()
        self._sleep, self.max_attempts, self.timeout = sleep, max_attempts, timeout

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             tool_choice: Any = None) -> LLMReply:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "auto"
        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            try:
                r = self.http.post(self.url, headers={"Authorization": f"Bearer {self.api_key}"},
                                   json=body, timeout=self.timeout)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                err = LLMError("network_error", str(e) or type(e).__name__, retryable=True)
            else:
                if r.status_code == 200:
                    return self._parse(r)
                err = self._error(r)
                ra = r.headers.get("retry-after")
                retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
            if not err.retryable or attempt == self.max_attempts:
                raise err
            self._sleep(min(retry_after, 30.0) if retry_after is not None
                        else min(30.0, 1.0 * 2 ** (attempt - 1)) + random.uniform(0, 0.3))
        raise AssertionError("unreachable")

    @staticmethod
    def _error(r: httpx.Response) -> LLMError:
        try:
            e = r.json().get("error", {})
        except (ValueError, AttributeError):
            e = {}
        code = e.get("code") if isinstance(e, dict) else None
        message = (e.get("message") if isinstance(e, dict) else None) or r.text[:300]
        return LLMError(str(code or f"http_{r.status_code}"), message, status=r.status_code,
                        retryable=r.status_code in RETRYABLE)

    @staticmethod
    def _parse(r: httpx.Response) -> LLMReply:
        try:
            d = r.json()
            choice = d["choices"][0]
            msg = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError("bad_response", f"Unexpected LLM response: {r.text[:200]!r}") from e
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function") or {}
            raw = fn.get("arguments") or "{}"
            args, perr = {}, None
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict):
                    args = parsed
                else:
                    perr = "arguments must be a JSON object"
            except ValueError as e:
                perr = f"arguments are not valid JSON: {e}"
            calls.append(ToolCall(c.get("id") or f"call_{i}", fn.get("name", ""), args,
                                  raw if isinstance(raw, str) else json.dumps(raw), perr))
        return LLMReply(msg.get("content"), calls, choice.get("finish_reason"), d.get("usage") or {},
                            msg.get("reasoning") or msg.get("reasoning_content"))