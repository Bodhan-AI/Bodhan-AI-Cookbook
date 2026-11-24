"""Claude client for the judge, using the official Anthropic SDK.

Same interface as `LLMClient.chat_json`, so the judge does not care which one it gets.
Differences from the OpenAI-compatible client:
  * structured outputs (`output_config.format`) guarantee schema-valid JSON when a schema
    is given;
  * no `temperature`: current Claude models reject non-default sampling parameters, so
    run-to-run determinism comes from the cache (and is measured by judge consistency);
  * a `refusal` stop reason or a reply cut off at `max_tokens` raises instead of
    returning partial output.
Reads ANTHROPIC_API_KEY (via the SDK), LLM_MODEL and optionally LLM_EFFORT.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import anthropic

from bodhan_eval.cache import Cache, canonical_json, sha256_bytes
from bodhan_eval.clients.http import CacheMiss
from bodhan_eval.clients.llm import parse_json_object
from bodhan_eval.cost import CostMeter


class LLMOutputError(RuntimeError):
    """The model returned no usable answer (refusal, truncated reply)."""


class ClaudeLLMClient:
    def __init__(
        self,
        cache: Cache,
        cost: CostMeter,
        *,
        model: str | None = None,
        api_key: str | None = None,
        effort: str | None = None,
        concurrency: int = 8,
        offline: bool = False,
        client: Any = None,
    ):
        self.cache = cache
        self.cost = cost
        self.offline = offline
        self.model = model or os.environ.get("LLM_MODEL") or ""
        self.effort = effort or os.environ.get("LLM_EFFORT") or None
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, max_retries=5, timeout=180.0
        )
        self._sem = asyncio.Semaphore(concurrency)

    @property
    def configured(self) -> bool:
        return bool(self.model)

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        sample: int = 0,
        max_tokens: int = 16000,
        tag: str = "judge",
        schema: dict | None = None,
    ) -> tuple[Any, bool]:
        """Return (parsed JSON, cached). OpenAI-style messages: `system` entries become the
        top-level system prompt. `sample` separates deliberate re-judgements in the cache."""
        payload = {
            "provider": "anthropic",
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "effort": self.effort,
            "schema_sha": sha256_bytes(canonical_json(schema).encode()) if schema else None,
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

        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": convo,
        }
        if system:
            kwargs["system"] = system
        output_config: dict[str, Any] = {}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if self.effort:
            output_config["effort"] = self.effort
        if output_config:
            kwargs["output_config"] = output_config

        async with self._sem:
            resp = await self._client.messages.create(**kwargs)

        usage = resp.usage
        tin = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        tout = usage.output_tokens or 0
        self.cost.record_llm(tin, tout, cached=False)

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            cat = getattr(details, "category", None) if details else None
            raise LLMOutputError(f"model declined the request (category: {cat})")
        if resp.stop_reason == "max_tokens":
            raise LLMOutputError(f"reply cut off at max_tokens={max_tokens}")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        parsed = json.loads(text) if schema else parse_json_object(text)
        self.cache.put_json(
            "llm",
            payload,
            parsed,
            {"raw": text[:4000], "tokens_in": tin, "tokens_out": tout, "request_id": resp.id},
        )
        return parsed, False
