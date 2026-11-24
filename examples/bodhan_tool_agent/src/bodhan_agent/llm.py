"""Thin wrapper around OpenAI-compatible chat endpoints with tool calling, plus optional fallbacks.

Only standard chat-completions features are used, so any compatible provider works.

The main endpoint comes from LLM_BASE_URL, LLM_MODEL and LLM_API_KEY (the variables the
assignment requires). Optional fallbacks come from LLM_FALLBACK_BASE_URL / _MODEL / _API_KEY
and LLM_FALLBACK2_BASE_URL / _MODEL / _API_KEY.

Every call goes to the first endpoint that is not cooling down. An endpoint that is rate-limited
cools down for as long as the provider asks (so the main model is used again as soon as its
limit resets); one that fails in another way (overloaded, unreachable, rejects the request) cools
down for FAILURE_COOLDOWN_S. Only when every endpoint is cooling down does the agent wait, at most
RATE_LIMIT_WAIT_S. Without fallback variables, behaviour is a single endpoint that waits out
short rate limits.

Free tiers limit tokens per minute and per day for each model separately, so a second model from
the same provider (for example a smaller one) is a useful fallback: it has its own allowance.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

log = logging.getLogger("bodhan_agent.llm")


class LLMError(Exception):
    pass


RATE_LIMIT_WAIT_S = 75.0  # the longest one call waits when every endpoint is rate-limited
FAILURE_COOLDOWN_S = 300.0  # how long an endpoint that failed (not rate-limited) is left alone
GLITCH_TRIES = 3  # attempts on one endpoint when the model keeps emitting malformed tool calls
GLITCH_ROUNDS = 2  # rounds over all endpoints before giving up on malformed tool calls


def _retry_after(exc) -> float | None:
    """Seconds the provider asked us to wait, from the Retry-After header (not capped)."""
    response = getattr(exc, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return max(1.0, float(value)) if value else None
    except ValueError:
        return None


def _is_tool_call_glitch(exc) -> bool:
    """The model produced a malformed tool call, which the provider rejected (e.g. Groq's gpt-oss
    calling a tool named 'commentary'). This is random, so asking again usually works."""
    text = str(exc).lower()
    return "tool call validation" in text or "tool_use_failed" in text or "failed to call a function" in text


@dataclass
class Endpoint:
    name: str  # "main" or "fallback"
    base_url: str
    model: str
    api_key: str
    timeout_s: float = 60.0
    max_retries: int = 2
    send_temperature: bool = True
    client: object = field(default=None, repr=False)
    cool_until: float = 0.0  # not used before this time (clock seconds)
    text_history: bool = False  # this provider only accepted earlier tool calls written out as text

    def __post_init__(self) -> None:
        if self.client is None:
            from openai import OpenAI

            self.client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout_s, max_retries=self.max_retries)


@dataclass
class LLM:
    endpoints: list[Endpoint]
    calls: int = 0
    tokens: dict = field(default_factory=lambda: {"prompt": 0, "completion": 0})
    usage: dict = field(default_factory=dict)  # per model: calls answered and failures
    active: int = 0  # index of the endpoint currently in use
    sleep: object = field(default=time.sleep, repr=False)  # replaced in tests so they do not wait
    clock: object = field(default=time.monotonic, repr=False)

    @classmethod
    def from_env(cls) -> LLM:
        missing = [name for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY") if not os.environ.get(name)]
        if missing:
            raise LLMError("Missing environment variables: " + ", ".join(missing))
        extra = []
        for prefix, label in (("LLM_FALLBACK_", "fallback"), ("LLM_FALLBACK2_", "fallback2")):
            parts = [os.environ.get(f"{prefix}{part}", "").strip() for part in ("BASE_URL", "MODEL", "API_KEY")]
            if all(parts):
                extra.append((label, parts))
            elif any(parts):
                log.warning("%s LLM ignored: set all three of %sBASE_URL, %sMODEL and %sAPI_KEY.", label, prefix, prefix, prefix)
        # Rate limits and retries are handled here (visibly), so with fallbacks the client itself
        # does not retry: hidden retries made long silent pauses. (Set before the client is created.)
        main_retries = 0 if extra else 2
        endpoints = [Endpoint("main", os.environ["LLM_BASE_URL"], os.environ["LLM_MODEL"], os.environ["LLM_API_KEY"], max_retries=main_retries)]
        endpoints += [Endpoint(label, *parts, max_retries=0) for label, parts in extra]
        return cls(endpoints)

    @property
    def model(self) -> str:
        return self.endpoints[self.active].model

    def _record(self, endpoint: Endpoint, key: str) -> None:
        row = self.usage.setdefault(f"{endpoint.name}:{endpoint.model}", {"calls": 0, "failures": 0})
        row[key] += 1

    def _create(self, endpoint: Endpoint, messages: list[dict], tools: list[dict]):
        from openai import BadRequestError

        kwargs = {"model": endpoint.model, "messages": messages, "tools": tools, "tool_choice": "auto"}
        if endpoint.send_temperature:
            kwargs["temperature"] = 0
        for attempt in range(GLITCH_TRIES):
            try:
                return endpoint.client.chat.completions.create(**kwargs)
            except BadRequestError as exc:
                if _is_tool_call_glitch(exc):
                    if attempt + 1 == GLITCH_TRIES:
                        raise
                    # At temperature 0 the model tends to repeat the same malformed call, so vary it.
                    log.warning("LLM %s (%s) sent a malformed tool call; asking again.", endpoint.name, endpoint.model)
                    kwargs["temperature"] = 0.7
                    continue
                if not endpoint.send_temperature or "temperature" not in str(exc).lower():
                    raise
                # Some models reject a temperature setting. Drop it and try once more.
                endpoint.send_temperature = False
                kwargs.pop("temperature", None)
                return endpoint.client.chat.completions.create(**kwargs)
        raise LLMError("unreachable")

    def _create_portably(self, endpoint: Endpoint, messages: list[dict], tools: list[dict]):
        """Call one endpoint. If it rejects a conversation that holds earlier tool calls (providers
        differ in what they accept there, e.g. when a fallback takes over mid-task), send the same
        conversation once more with those tool calls written out as plain text."""
        from openai import BadRequestError

        if endpoint.text_history:
            return self._create(endpoint, flatten_tool_history(messages), tools)
        try:
            return self._create(endpoint, messages, tools)
        except BadRequestError as exc:
            if _is_tool_call_glitch(exc) or not any(m.get("role") == "tool" for m in messages):
                raise
            response = self._create(endpoint, flatten_tool_history(messages), tools)
            endpoint.text_history = True  # remember, so later calls do not send the rejected format first
            log.warning("LLM %s (%s) needs earlier tool calls as text; sending them that way from now on.", endpoint.name, endpoint.model)
            return response

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """Send the conversation and return the assistant message as a plain dict."""
        from openai import APIError, BadRequestError, RateLimitError

        errors: list[str] = []
        waited = 0.0
        glitch_rounds = 0
        while True:
            for index, endpoint in enumerate(self.endpoints):
                if endpoint.cool_until > self.clock():
                    continue
                try:
                    response = self._create_portably(endpoint, messages, tools)
                except RateLimitError as exc:
                    delay = _retry_after(exc) or 20.0
                    endpoint.cool_until = self.clock() + delay
                    self._record(endpoint, "failures")
                    errors.append(f"{endpoint.name} ({endpoint.model}): rate limit, retry in {delay:.0f} s")
                    what = "probably its daily limit" if delay > RATE_LIMIT_WAIT_S else "its per-minute limit"
                    log.warning("LLM %s (%s) hit %s; resting it for %.0f s.", endpoint.name, endpoint.model, what, delay)
                    continue
                except APIError as exc:
                    self._record(endpoint, "failures")
                    errors.append(f"{endpoint.name} ({endpoint.model}): {exc.__class__.__name__}: {exc}")
                    if isinstance(exc, BadRequestError) and _is_tool_call_glitch(exc):
                        log.warning("LLM %s (%s) kept sending malformed tool calls; trying the next model.", endpoint.name, endpoint.model)
                        continue  # random, so no rest: it can be asked again in the next round
                    endpoint.cool_until = self.clock() + FAILURE_COOLDOWN_S
                    log.warning("LLM %s (%s) failed with %s; resting it for %.0f s.", endpoint.name, endpoint.model, exc.__class__.__name__, FAILURE_COOLDOWN_S)
                    continue
                if index != self.active:
                    log.warning("LLM call answered by %s (%s).", endpoint.name, endpoint.model)
                    self.active = index
                return self._message(endpoint, response)
            soonest = min(e.cool_until for e in self.endpoints) - self.clock()
            if soonest <= 0:
                glitch_rounds += 1
                if glitch_rounds >= GLITCH_ROUNDS:
                    raise LLMError("Every LLM endpoint failed. " + " | ".join(errors[-6:]))
                continue
            if waited + soonest > RATE_LIMIT_WAIT_S:
                raise LLMError("Every LLM endpoint failed. " + " | ".join(errors[-6:]))
            log.warning("Every LLM endpoint is resting; waiting %.0f s.", soonest)
            self.sleep(soonest)
            waited += soonest

    def _message(self, endpoint: Endpoint, response) -> dict:
        self._record(endpoint, "calls")
        self.calls += 1
        if response.usage:
            self.tokens["prompt"] += response.usage.prompt_tokens or 0
            self.tokens["completion"] += response.usage.completion_tokens or 0
        message = response.choices[0].message
        calls = [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}} for c in (message.tool_calls or [])]
        out = {"role": "assistant", "content": message.content or ""}
        if calls:
            out["tool_calls"] = calls
        return out


def flatten_tool_history(messages: list[dict]) -> list[dict]:
    """The same conversation with tool calls and results as ordinary text messages."""
    out: list[dict] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            lines = [m.get("content") or ""] + [f"[I called the tool {c['function']['name']} with {c['function']['arguments']}]" for c in m["tool_calls"]]
            item = {"role": "assistant", "content": "\n".join(line for line in lines if line).strip()}
        elif m.get("role") == "tool":
            item = {"role": "user", "content": f"[Tool result]\n{m.get('content') or ''}"}
        elif m.get("role") == "assistant" and not m.get("content"):
            continue
        else:
            item = dict(m)
        if out and out[-1]["role"] == item["role"] and item["role"] in ("user", "assistant"):
            out[-1] = {**out[-1], "content": f"{out[-1]['content']}\n\n{item['content']}"}
        else:
            out.append(item)
    return out
