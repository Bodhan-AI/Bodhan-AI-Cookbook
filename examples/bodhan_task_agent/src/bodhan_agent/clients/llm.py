from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic, perf_counter
from typing import Any

import openai
from pydantic import ValidationError

from bodhan_agent.core import guards
from bodhan_agent.core.config import LlmSettings
from bodhan_agent.schemas.llm import FinishReason, LlmReply, ToolCall
from bodhan_agent.schemas.trace import LlmTokens

MESSAGE_ROLES: frozenset[str] = frozenset({"system", "user", "assistant", "tool"})
DROPPABLE_PARAMS: tuple[str, ...] = ("parallel_tool_calls", "temperature", "tool_choice", "max_tokens")
FINISH_REASONS: dict[str, FinishReason] = {
    "stop": "stop",
    "end_turn": "stop",
    "tool_calls": "tool_calls",
    "tool_use": "tool_calls",
    "length": "length",
    "max_tokens": "length",
    "content_filter": "content_filter",
}
MAX_BACKOFF_S = 8.0
MISSING_TOOL_NAME = "missing_tool_name"
_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    asyncio.TimeoutError,
)
_UNAVAILABLE = (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)
_REJECTED = (openai.BadRequestError, openai.UnprocessableEntityError)


class LlmError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def check_messages(messages: Sequence[dict[str, Any]]) -> None:
    for index, message in enumerate(messages):
        role = message.get("role")
        if role not in MESSAGE_ROLES:
            raise ValueError(f"message {index} has unsupported role {role!r}.")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError(f"message {index} content must be text; images, audio and parts are never sent.")


def _parse_arguments(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    if not raw.strip():
        return {}, None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        return None, f"arguments are not valid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "arguments must be a JSON object."
    return value, None


def _finish_reason(raw: Any) -> FinishReason:
    return FINISH_REASONS.get(str(raw).lower(), "other") if raw else "other"


def _tokens(usage: Any) -> LlmTokens:
    if usage is None:
        return LlmTokens()
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    return LlmTokens(
        prompt=prompt if isinstance(prompt, int) and prompt >= 0 else None,
        completion=completion if isinstance(completion, int) and completion >= 0 else None,
    )


def _unique_id(raw_id: Any, turn: int, index: int, seen: set[str]) -> str:
    if isinstance(raw_id, str) and raw_id and raw_id not in seen:
        return raw_id
    base = f"call_{turn}_{index}"
    candidate = base
    suffix = 1
    while candidate in seen:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _tool_calls(raw_calls: Any, turn: int) -> list[ToolCall]:
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_calls or []):
        call_id = _unique_id(getattr(raw, "id", None), turn, index, seen)
        seen.add(call_id)
        function = getattr(raw, "function", None)
        name = getattr(function, "name", None)
        raw_arguments = getattr(function, "arguments", None)
        raw_arguments = raw_arguments if isinstance(raw_arguments, str) else ""
        if not isinstance(name, str) or not name.strip():
            calls.append(
                ToolCall(
                    id=call_id,
                    name=MISSING_TOOL_NAME,
                    raw_arguments=raw_arguments,
                    parse_error="the tool call has no tool name.",
                )  # fmt: skip
            )
            continue
        arguments, error = _parse_arguments(raw_arguments)
        calls.append(
            ToolCall(id=call_id, name=name.strip(), raw_arguments=raw_arguments, arguments=arguments, parse_error=error)
        )
    return calls


def _rejected_param(message: str, kwargs: dict[str, Any]) -> str | None:
    lowered = message.lower()
    for param in DROPPABLE_PARAMS:
        if param in kwargs and param in lowered:
            return param
    return None


class LlmClient:
    def __init__(
        self,
        settings: LlmSettings,
        client: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._client = client or openai.AsyncOpenAI(
            api_key=settings.api_key or "missing",
            base_url=settings.base_url or None,
            timeout=settings.timeout_s,
            max_retries=0,
        )
        self._sleep = sleep
        self._dropped: set[str] = set()
        self._max_tokens_param = "max_tokens"
        self._turn = 0

    def _clean(self, text: str) -> str:
        return guards.scrub_secrets(text, [self.settings.api_key])

    def _request(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]] | None, tool_choice: str
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.settings.model, "messages": list(messages)}
        if tools:
            kwargs.update(tools=list(tools), tool_choice=tool_choice, parallel_tool_calls=True)
        if self.settings.temperature is not None:
            kwargs["temperature"] = self.settings.temperature
        kwargs[self._max_tokens_param] = self.settings.max_output_tokens
        for param in self._dropped:
            kwargs.pop(param, None)
        return kwargs

    def _adapt(self, param: str | None, text: str, kwargs: dict[str, Any]) -> bool:
        if param == "max_tokens" and self._max_tokens_param == "max_tokens" and "max_completion_tokens" in text:
            self._max_tokens_param = "max_completion_tokens"
            return True
        if param is None or param in self._dropped:
            return False
        if param == "tool_choice" and kwargs.get("tool_choice") != "auto":
            return False
        if param == "max_tokens":
            self._dropped.add(self._max_tokens_param)
        self._dropped.add(param)
        return True

    def _timeout(self, deadline: float | None) -> float:
        timeout = self.settings.timeout_s
        if deadline is not None:
            timeout = min(timeout, deadline - monotonic())
        if timeout <= 0:
            raise LlmError("llm_timeout", "No time is left in the run for another LLM call.")
        return timeout

    def _reply(self, response: Any, attempts: int, started: float) -> LlmReply:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("the provider returned no choices.")
        choice = choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        return LlmReply(
            content=content if isinstance(content, str) else None,
            tool_calls=_tool_calls(getattr(message, "tool_calls", None), self._turn),
            tokens=_tokens(getattr(response, "usage", None)),
            finish_reason=_finish_reason(getattr(choice, "finish_reason", None)),
            model=getattr(response, "model", None),
            latency_ms=(perf_counter() - started) * 1000,
            attempts=attempts,
            dropped_params=sorted(self._dropped),
        )

    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        *,
        tool_choice: str = "auto",
        deadline: float | None = None,
    ) -> LlmReply:
        if not self.settings.configured:
            raise LlmError("llm_unavailable", f"LLM is not configured: set {', '.join(self.settings.missing)}.")
        check_messages(messages)
        self._turn += 1
        started = perf_counter()
        attempts = 0
        last = LlmError("llm_error", "The LLM call did not run.")
        while attempts <= self.settings.max_retries:
            attempts += 1
            kwargs = self._request(messages, tools, tool_choice)
            timeout = self._timeout(deadline)
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(**kwargs, timeout=timeout), timeout
                )
            except _REJECTED as exc:
                text = self._clean(str(exc))
                if self._adapt(_rejected_param(text, kwargs), text.lower(), kwargs):
                    attempts -= 1
                    continue
                if tools and "tool" in text.lower():
                    raise LlmError("llm_unavailable", f"The LLM provider rejected tool calling: {text}") from exc
                raise LlmError("llm_error", f"The LLM provider rejected the request: {text}") from exc
            except _UNAVAILABLE as exc:
                raise LlmError("llm_unavailable", f"The LLM provider refused access: {self._clean(str(exc))}") from exc
            except _RETRYABLE as exc:
                timed_out = isinstance(exc, (asyncio.TimeoutError, openai.APITimeoutError))
                message = self._clean(str(exc)) or type(exc).__name__
                last = LlmError("llm_timeout" if timed_out else "llm_retryable", message, retryable=True)
            except openai.APIStatusError as exc:
                if exc.status_code < 500:
                    raise LlmError("llm_error", f"The LLM provider returned an error: {self._clean(str(exc))}") from exc
                last = LlmError("llm_retryable", self._clean(str(exc)), retryable=True)
            else:
                try:
                    return self._reply(response, attempts, started)
                except (ValidationError, ValueError) as exc:
                    last = LlmError("llm_bad_reply", f"The LLM returned an unusable reply: {exc}", retryable=True)
            if attempts <= self.settings.max_retries:
                backoff = min(MAX_BACKOFF_S, 2.0 ** (attempts - 1))
                if deadline is not None:
                    backoff = min(backoff, max(0.0, deadline - monotonic()))
                await self._sleep(backoff)
        raise last

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()
