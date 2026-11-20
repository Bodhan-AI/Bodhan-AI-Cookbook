"""OpenAI-compatible LLM client for the judge and orchestration decisions.

Reads LLM_BASE_URL, LLM_MODEL, LLM_API_KEY. Uses only portable chat-completions features:
JSON mode is attempted once and silently dropped if the provider rejects it.
The LLM only READS Indian-language text; prompts never ask it to produce any.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import openai

from bodhan_eval.cache import Cache
from bodhan_eval.clients.http import CacheMiss
from bodhan_eval.cost import CostMeter


def parse_json_object(text: str) -> Any:
    """Parse the first JSON object/array in a model reply (tolerates ```json fences, prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        s, e = text.find(open_ch), text.rfind(close_ch)
        if s != -1 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON object in LLM reply: {text[:200]!r}")


class LLMClient:
    def __init__(
        self,
        cache: Cache,
        cost: CostMeter,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        concurrency: int = 8,
        offline: bool = False,
        client: Any = None,
    ):
        self.cache = cache
        self.cost = cost
        self.offline = offline
        self.model = model or os.environ.get("LLM_MODEL") or ""
        self._client = client or openai.AsyncOpenAI(
            base_url=base_url or os.environ.get("LLM_BASE_URL") or None,
            api_key=api_key or os.environ.get("LLM_API_KEY") or "unset",
            max_retries=5,
            timeout=180.0,
        )
        self._sem = asyncio.Semaphore(concurrency)
        self._json_mode = True

    @property
    def configured(self) -> bool:
        return bool(self.model)

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        sample: int = 0,
        max_tokens: int = 2000,
        tag: str = "judge",
        schema: dict | None = None,
    ) -> tuple[Any, bool]:
        """Return (parsed JSON, cached). `sample` distinguishes deliberate re-judgements
        (for consistency measurement) in the cache key; temperature is always 0.
        `schema` is accepted for interface parity with ClaudeLLMClient; this client relies on
        JSON mode plus the prompt, and the caller validates the result."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "sample": sample,
            "tag": tag,
        }
        hit = self.cache.get_json("llm", payload)
        if hit:
            self.cost.record_llm(0, 0, cached=True)
            return hit[0], True
        if self.offline:
            raise CacheMiss("llm: not in cache and running offline")
        if not self.configured:
            raise RuntimeError("LLM_MODEL is not set")

        async with self._sem:
            resp = await self._create(messages, max_tokens)
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "prompt_tokens", 0) or 0
        tout = getattr(usage, "completion_tokens", 0) or 0
        self.cost.record_llm(tin, tout, cached=False)
        parsed = parse_json_object(text)
        self.cache.put_json(
            "llm", payload, parsed, {"raw": text[:4000], "tokens_in": tin, "tokens_out": tout}
        )
        return parsed, False

    async def _create(self, messages, max_tokens):
        kwargs = dict(model=self.model, messages=messages, temperature=0, max_tokens=max_tokens)
        if self._json_mode:
            try:
                return await self._client.chat.completions.create(
                    **kwargs, response_format={"type": "json_object"}
                )
            except openai.BadRequestError:
                self._json_mode = False  # provider lacks JSON mode; fall back to prompt-only
        return await self._client.chat.completions.create(**kwargs)
