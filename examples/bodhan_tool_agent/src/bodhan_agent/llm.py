"""Thin wrapper over any OpenAI-compatible chat endpoint (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY)."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any


log = logging.getLogger("bodhan.llm")


@dataclass
class LLMReply:
    content: str | None
    tool_calls: list[dict] = field(default_factory=list)  # [{"id", "name", "arguments"(str)}]


class LLM:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None):
        from openai import OpenAI

        self.model = model or os.environ.get("LLM_MODEL", "")
        key = api_key or os.environ.get("LLM_API_KEY", "")
        if not self.model or not key:
            raise SystemExit("LLM_MODEL and LLM_API_KEY must be set (LLM_BASE_URL optional). See .env.example.")
        self.client = OpenAI(base_url=base_url or os.environ.get("LLM_BASE_URL") or None, api_key=key,
                             timeout=120, max_retries=2)

    def chat(self, messages: list[dict], tools: list[dict], force_tool: str | None = None) -> LLMReply:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "tools": tools}
        if force_tool:
            kwargs["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
        r = self._create(kwargs)
        m = r.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"}
                 for c in (m.tool_calls or [])]
        return LLMReply(m.content, calls)

    def _create(self, kwargs: dict, attempts: int = 8):
        """Rate limits (tokens-per-minute on shared providers) are routine for an agent that resends its history each
        turn, so wait out the provider's retry-after hint instead of failing the whole task."""
        from openai import APIConnectionError, InternalServerError, RateLimitError

        for i in range(attempts):
            try:
                return self.client.chat.completions.create(**kwargs)
            except (RateLimitError, InternalServerError, APIConnectionError) as e:
                if i == attempts - 1:
                    raise
                hint = None
                resp = getattr(e, "response", None)
                if resp is not None:
                    try:
                        hint = float(resp.headers.get("retry-after", ""))
                    except ValueError:
                        hint = None
                wait = min(hint + 1 if hint else 10 * (2 ** min(i, 3)), 90)
                log.warning("LLM %s; waiting %.0fs (attempt %d/%d)", type(e).__name__, wait, i + 1, attempts)
                time.sleep(wait)
