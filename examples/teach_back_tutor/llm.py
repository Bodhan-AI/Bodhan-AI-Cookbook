"""Companion LLM for reasoning (lesson card, dialogue, scoring).

Any OpenAI-compatible endpoint works: set LLM_BASE_URL / LLM_MODEL in .env
(defaults to OpenAI). Bodhan calls never go through here.
"""

from __future__ import annotations

import json
import os
import re
import time

from openai import APIConnectionError, BadRequestError, InternalServerError, OpenAI, RateLimitError

# seconds spent waiting on rate limits, so latency can be reported with and without free-tier throttling
STATS = {"calls": 0, "throttled_seconds": 0.0}


def _client() -> OpenAI:
    # retries are handled in _create, so the SDK's own hidden retries are off
    return OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ.get("LLM_BASE_URL") or None, max_retries=0)


def _model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


def _wait_seconds(err: Exception, attempt: int) -> float:
    """Wait as long as the server asks (retry-after), but at least a growing floor: parallel calls that all
    retry after the same short delay just collide again."""
    floor = min(30.0, 2.0 ** (attempt + 1))  # 2, 4, 8, 16, 30 s: about a minute, the window free tiers count in
    try:
        return max(floor, min(60.0, float(err.response.headers["retry-after"])) + 0.5)
    except (AttributeError, KeyError, TypeError, ValueError):
        return max(floor, 5.0 * (attempt + 1))


def _create(model: str | None = None, attempts: int = 6, **kwargs):
    """Free-tier endpoints rate-limit and get overloaded; back off a few times, then give up.
    `attempts=1` fails fast, for calls that have a cheap fallback (the intent matcher)."""
    effort = os.environ.get("LLM_REASONING_EFFORT")
    if effort:
        kwargs["reasoning_effort"] = effort
    STATS["calls"] += 1
    for attempt in range(attempts):
        try:
            return _client().chat.completions.create(model=model or _model(), **kwargs)
        except BadRequestError as err:  # gpt-oss now and then "calls a tool" it was never given; just ask again
            if "called a tool" not in str(err) or attempt == attempts - 1:
                raise
            continue
        except (RateLimitError, InternalServerError, APIConnectionError) as err:
            if attempt == attempts - 1:
                raise
            wait = _wait_seconds(err, attempt)
            STATS["throttled_seconds"] += wait
            time.sleep(wait)


def chat(messages: list[dict], temperature: float = 0.7, model: str | None = None) -> str:
    return _create(model, messages=messages, temperature=temperature).choices[0].message.content.strip()


def chat_json(messages: list[dict], temperature: float = 0.2, model: str | None = None, attempts: int = 6) -> dict:
    """Ask for a JSON object and parse it. Models occasionally wrap it in a list or break it; try up to 3 times.

    Raises ValueError if every try is broken (callers fall back to a safe default)."""
    text = ""
    for _ in range(3):
        try:
            resp = _create(model, attempts, messages=messages, temperature=temperature,
                           response_format={"type": "json_object"})
        except BadRequestError as err:  # Groq validates JSON mode itself and returns 400 on broken output
            if "pars" not in str(err).lower() and "json" not in str(err).lower():
                raise
            continue
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        text = re.sub(r",\s*([}\]])", r"\1", text)  # trailing commas
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        if isinstance(data, dict):
            return data
    raise ValueError(f"no valid JSON object after 3 tries: {text[:200]}")
