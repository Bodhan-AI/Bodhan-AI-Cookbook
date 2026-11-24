"""Companion LLM for reasoning (lesson card, dialogue, scoring).

Any OpenAI-compatible endpoint works: set LLM_BASE_URL / LLM_MODEL in .env
(defaults to OpenAI). Bodhan calls never go through here.
"""

from __future__ import annotations

import json
import os
import re
import time

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

# seconds spent waiting on rate limits, so latency can be reported with and without free-tier throttling
STATS = {"calls": 0, "throttled_seconds": 0.0}


def _client() -> OpenAI:
    # retries are handled in _create, so the SDK's own hidden retries are off
    return OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ.get("LLM_BASE_URL") or None, max_retries=0)


def _model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


def _wait_seconds(err: Exception, attempt: int) -> float:
    """Wait as long as the server asks (retry-after), else back off linearly."""
    try:
        return min(60.0, float(err.response.headers["retry-after"])) + 0.5
    except (AttributeError, KeyError, TypeError, ValueError):
        return 5.0 * (attempt + 1)


def _create(**kwargs):
    """Free-tier endpoints rate-limit and get overloaded; back off a few times, then give up."""
    effort = os.environ.get("LLM_REASONING_EFFORT")
    if effort:
        kwargs["reasoning_effort"] = effort
    STATS["calls"] += 1
    for attempt in range(5):
        try:
            return _client().chat.completions.create(model=_model(), **kwargs)
        except (RateLimitError, InternalServerError, APIConnectionError) as err:
            if attempt == 4:
                raise
            wait = _wait_seconds(err, attempt)
            STATS["throttled_seconds"] += wait
            time.sleep(wait)


def chat(messages: list[dict], temperature: float = 0.7) -> str:
    return _create(messages=messages, temperature=temperature).choices[0].message.content.strip()


def chat_json(messages: list[dict], temperature: float = 0.2) -> dict:
    """Ask for a JSON object and parse it. Small models occasionally wrap it in a list or break it; retry once."""
    for attempt in range(2):
        resp = _create(messages=messages, temperature=temperature, response_format={"type": "json_object"})
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        text = re.sub(r",\s*([}\]])", r"\1", text)  # trailing commas
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            if attempt:
                raise
            continue
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        if isinstance(data, dict):
            return data
        if attempt:
            raise ValueError(f"expected a JSON object, got: {text[:200]}")
    raise AssertionError("unreachable")
