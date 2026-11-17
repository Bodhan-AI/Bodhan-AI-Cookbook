"""OpenAI-compatible chat-completions client for the judge, configured only by LLM_BASE_URL, LLM_MODEL, LLM_API_KEY.

Structured output is requested with response_format={"type": "json_object"}. Providers that
reject that (or temperature) get the same request without it, and the reply is parsed with a
tolerant JSON extractor. The downgrade is remembered for the rest of the run.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import regex

from .. import log
from ..cache import Cache, make_key
from ..config import Settings
from .http import ServiceError, ServiceUnavailable, request

OPTIONAL_PARAMS = ("response_format", "reasoning_effort", "temperature", "seed")
MAX_TOKENS_CEILING = 16000


class LLMParseError(Exception):
    pass


def extract_json(text: str) -> Any:
    text = text.strip()
    fenced = regex.search(r"```(?:json)?\s*(.+?)```", text, regex.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, escape = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise LLMParseError(f"no JSON object in reply: {text[:200]!r}")


class LLMClient:
    def __init__(self, settings: Settings, cache: Cache, http: httpx.AsyncClient):
        self.settings = settings
        self.cache = cache
        self.http = http
        self.model = settings.llm_model or ""
        self.url = f"{(settings.llm_base_url or '').rstrip('/')}/chat/completions"
        self._sem = asyncio.Semaphore(settings.concurrency.get("llm", 6))
        self._dropped: set[str] = set()
        self.disabled: str | None = None if settings.llm_configured else "LLM_BASE_URL / LLM_MODEL not set"
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    @property
    def available(self) -> bool:
        return self.disabled is None

    async def complete_json(self, messages: list[dict], *, namespace: str, variant: int = 0, max_tokens: int = 8000) -> tuple[Any, bool]:
        """Return (parsed JSON, cached). `variant` > 0 gives independent cached samples for consistency tests."""
        key = make_key(namespace, self.model, messages, {"temperature": 0, "variant": variant, "effort": self.settings.llm_reasoning_effort})
        hit = self.cache.get(key)
        if hit is not None:
            return hit[0], True
        if self.settings.offline:
            raise ServiceUnavailable("LLM: offline cache miss; no API request was made")
        if not self.available:
            raise ServiceUnavailable(f"LLM unavailable: {self.disabled}")
        content = await self._chat(messages, max_tokens=max_tokens, variant=variant)
        try:
            parsed = extract_json(content)
        except LLMParseError:
            repair = messages + [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content": "That reply was not valid JSON. Reply with only the JSON object, nothing else."},
            ]
            parsed = extract_json(await self._chat(repair, max_tokens=max_tokens, variant=variant))
        self.cache.put(key, namespace, parsed, 0.0)
        return parsed, False

    async def _chat(self, messages: list[dict], *, max_tokens: int, variant: int) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if variant:
            body["seed"] = variant
        if self.settings.llm_reasoning_effort:
            body["reasoning_effort"] = self.settings.llm_reasoning_effort
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        for _ in range(len(OPTIONAL_PARAMS) + 2):
            payload = {k: v for k, v in body.items() if k not in self._dropped}
            try:
                async with self._sem:
                    resp = await request(
                        self.http,
                        "POST",
                        self.url,
                        service="llm",
                        headers=headers,
                        json=payload,
                        attempts=self.settings.max_attempts,
                        timeout=max(self.settings.timeout_s, 120.0),
                    )
            except ServiceUnavailable as exc:
                self.disabled = str(exc)
                raise
            except ServiceError as exc:
                droppable = next((p for p in OPTIONAL_PARAMS if p in payload and p in str(exc).lower()), None)
                if droppable is None and exc.status in (400, 422):
                    droppable = next((p for p in OPTIONAL_PARAMS if p in payload), None)
                if droppable is None:
                    raise
                self._dropped.add(droppable)
                log.warn("llm_param_dropped", param=droppable, error=str(exc)[:200])
                continue
            data = resp.json()
            usage = data.get("usage") or {}
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ServiceError(f"llm: malformed response: {str(data)[:200]}") from exc
            content = message.get("content") or ""
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            if not content.strip():
                finish = data["choices"][0].get("finish_reason")
                if finish == "length" and body["max_tokens"] < MAX_TOKENS_CEILING:
                    # Reasoning models can spend the whole budget thinking; give them room once.
                    body["max_tokens"] = min(body["max_tokens"] * 2, MAX_TOKENS_CEILING)
                    log.warn("llm_empty_reply_retry", max_tokens=body["max_tokens"])
                    continue
                raise ServiceError(f"llm: empty reply content (finish_reason={finish})")
            return content
        raise ServiceError("llm: request rejected even without optional parameters")
