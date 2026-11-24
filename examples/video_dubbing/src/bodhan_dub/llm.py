"""The orchestrator's LLM: any OpenAI-compatible chat endpoint.

Ground rule: the LLM only ever reads and writes English (plans, merges, glossary
terms, condensed source text, verdicts). Indian-language text comes only from Bodhan.

We ask for JSON in the prompt and parse it ourselves instead of relying on a
provider-specific `response_format`, so any endpoint works.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from openai import APIError, BadRequestError, OpenAI

from .cache import DiskCache
from .config import Settings

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """Parse the first JSON object or array in `text`, tolerating code fences and chatter."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(open_ch), cleaned.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"no JSON found in LLM reply: {text[:200]!r}")


class LLM:
    def __init__(self, settings: Settings, *, client: OpenAI | None = None, timeout_s: float = 120.0,
                 cache: DiskCache | None = None):
        self.model = settings.llm_model
        self.client = client or OpenAI(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=timeout_s, max_retries=4
        )
        # Cached replies make reruns free and the run deterministic: same prompt, same decision.
        self.cache = cache
        self.usage: dict[str, int] = {"calls": 0, "cache_hits": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self._usage_lock = threading.Lock()
        # Generation settings beyond model + messages. Not every OpenAI-compatible provider
        # accepts all of them, so the first 400 drops them and we retry with the bare request.
        self._optional: dict[str, Any] = {"temperature": 0}
        if settings.llm_reasoning_effort:
            self._optional["reasoning_effort"] = settings.llm_reasoning_effort
        if settings.llm_max_tokens:
            self._optional["max_tokens"] = settings.llm_max_tokens

    def complete_json(self, system: str, user: str, *, max_attempts: int = 3) -> Any:
        """Ask for a JSON answer; re-ask on unparseable replies, then raise LLMError.

        Any provider failure also surfaces as LLMError, so every caller has one thing to
        catch before taking its deterministic fallback.
        """
        key = DiskCache.key(f"llm:{self.model}", {}, json.dumps([system, user], ensure_ascii=False))
        if self.cache and (hit := self.cache.get_json(key)) is not None:
            self._count(cache_hits=1)
            return hit["reply"]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            text = self._chat(messages)
            try:
                reply = extract_json(text)
            except LLMError as exc:
                last_error = exc
                log.warning("llm reply was not JSON", extra={"attempt": attempt})
                messages = [*messages, {"role": "assistant", "content": text},
                            {"role": "user", "content": "Reply with valid JSON only, no prose."}]
                continue
            if self.cache:
                self.cache.put_json(key, {"reply": reply})
            return reply
        raise LLMError(str(last_error))

    def _count(self, **amounts: int) -> None:
        with self._usage_lock:
            for name, value in amounts.items():
                self.usage[name] += value

    def _chat(self, messages: list[dict[str, str]]) -> str:
        base: dict[str, Any] = {"model": self.model, "messages": messages}
        try:
            try:
                resp = self.client.chat.completions.create(**base, **self._optional)
            except BadRequestError as exc:
                if not self._optional:
                    raise
                log.warning("llm rejected optional settings; retrying without them",
                            extra={"dropped": sorted(self._optional), "error": str(exc)[:200]})
                self._optional = {}
                resp = self.client.chat.completions.create(**base)
        except APIError as exc:  # after the SDK's own retries: timeouts, 5xx, auth, bad requests
            raise LLMError(f"LLM request failed: {exc}") from exc
        usage = getattr(resp, "usage", None)
        self._count(calls=1, prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0)
        choice = resp.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason == "length":
            # Cut off at the token limit: an empty answer (a reasoning model spent the budget thinking)
            # or truncated JSON. Re-asking the same prompt would hit the same limit, so fail now and
            # let the caller shrink the request.
            what = "empty" if not content.strip() else "cut off"
            raise LLMError(f"LLM reply {what} at the token limit (raise LLM_MAX_TOKENS or lower effort)")
        return content
