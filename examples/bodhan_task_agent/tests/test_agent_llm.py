from __future__ import annotations

import asyncio
from time import monotonic
from types import SimpleNamespace as NS
from typing import Any

import httpx
import openai
import pytest
from pydantic import ValidationError

from bodhan_agent.clients.llm import MISSING_TOOL_NAME, LlmClient, LlmError, check_messages
from bodhan_agent.core.config import LlmSettings
from bodhan_agent.schemas.llm import LlmReply, ToolCall

SECRET = "sk-secret-key-123"
SETTINGS = LlmSettings(base_url="http://llm.test/v1", model="test-model", api_key=SECRET, max_retries=2)
USER = [{"role": "user", "content": "How many pages?"}]
TOOLS = [{"type": "function", "function": {"name": "ocr_document", "parameters": {"type": "object"}}}]


def response(
    content: str | None = None,
    calls: tuple[tuple[Any, Any, Any], ...] = (),
    finish: str | None = "stop",
    usage: Any = "default",
    choices: bool = True,
) -> NS:
    tool_calls = [NS(id=call_id, function=NS(name=name, arguments=arguments)) for call_id, name, arguments in calls]
    message = NS(content=content, tool_calls=tool_calls)
    return NS(
        choices=[NS(message=message, finish_reason=finish)] if choices else [],
        usage=NS(prompt_tokens=10, completion_tokens=5) if usage == "default" else usage,
        model="test-model",
    )


def api_error(cls: type[openai.APIStatusError], status: int, message: str) -> openai.APIStatusError:
    return cls(message, response=httpx.Response(status, request=httpx.Request("POST", "http://llm.test")), body=None)


class FakeProvider:
    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return await item()
        return item

    async def close(self) -> None:
        self.closed = True


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def client(provider: FakeProvider, settings: LlmSettings = SETTINGS) -> tuple[LlmClient, Sleeps]:
    sleeps = Sleeps()
    return LlmClient(settings, provider, sleeps), sleeps


def test_tool_call_schema_rules() -> None:
    assert ToolCall(id="c", name="read", raw_arguments="{}", arguments={}).arguments == {}
    assert ToolCall(id="c", name="read", raw_arguments="{x", parse_error="bad").arguments is None
    for fields in ({}, {"arguments": {}, "parse_error": "bad"}):
        with pytest.raises(ValidationError):
            ToolCall(id="c", name="read", raw_arguments="{}", **fields)
    with pytest.raises(ValidationError):
        ToolCall(id="c", name="", raw_arguments="{}", arguments={})


def test_reply_schema_rules() -> None:
    call = ToolCall(id="c1", name="read", raw_arguments='{"path": "/a"}', arguments={"path": "/a"})
    assert LlmReply(content="Done.", latency_ms=1, attempts=1).wants_tools is False
    assert LlmReply(tool_calls=[call], latency_ms=1, attempts=1).wants_tools is True
    with pytest.raises(ValidationError, match="neither tool calls nor usable content"):
        LlmReply(content="  ", latency_ms=1, attempts=1)
    with pytest.raises(ValidationError, match="duplicate"):
        LlmReply(tool_calls=[call, call], latency_ms=1, attempts=1)
    with pytest.raises(ValidationError):
        LlmReply(content="x", latency_ms=-1, attempts=1)


def test_assistant_message_keeps_raw_arguments() -> None:
    raw = '{"path":   "/a"}'
    call = ToolCall(id="c1", name="read", raw_arguments=raw, arguments={"path": "/a"})
    message = LlmReply(tool_calls=[call], latency_ms=1, attempts=1).assistant_message()
    assert message == {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": raw}}],
    }


def test_check_messages_text_only() -> None:
    check_messages([{"role": "system", "content": "x"}, {"role": "assistant", "content": None}])
    with pytest.raises(ValueError, match="must be text"):
        check_messages([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}])
    with pytest.raises(ValueError, match="unsupported role"):
        check_messages([{"role": "developer", "content": "x"}])


async def test_media_never_reaches_provider() -> None:
    provider = FakeProvider()
    llm, _ = client(provider)
    with pytest.raises(ValueError):
        await llm.complete([{"role": "user", "content": [{"type": "input_audio"}]}])
    assert provider.calls == []


async def test_unconfigured_llm_reports_missing_values() -> None:
    llm, _ = client(FakeProvider(), LlmSettings(base_url="", model="", api_key=""))
    with pytest.raises(LlmError) as error:
        await llm.complete(USER)
    assert error.value.code == "llm_unavailable"
    assert "LLM_BASE_URL" in error.value.message


async def test_request_shape_with_tools() -> None:
    provider = FakeProvider(response("ok"))
    llm, _ = client(provider)
    await llm.complete(USER, TOOLS, deadline=monotonic() + 30)
    sent = provider.calls[0]
    assert sent["model"] == "test-model" and sent["messages"] == USER and sent["tools"] == TOOLS
    assert (sent["tool_choice"], sent["parallel_tool_calls"], sent["temperature"], sent["max_tokens"]) == (
        "auto",
        True,
        0.0,
        4096,
    )
    assert 0 < sent["timeout"] <= 30


async def test_request_without_tools_or_temperature() -> None:
    provider = FakeProvider(response("ok"))
    llm, _ = client(provider, LlmSettings(base_url="http://x", model="m", api_key="k", temperature=None))
    await llm.complete(USER)
    assert not {"tools", "tool_choice", "parallel_tool_calls", "temperature"} & set(provider.calls[0])


async def test_reply_normalized() -> None:
    llm, _ = client(FakeProvider(response("Plan ready.", finish="stop")))
    reply = await llm.complete(USER)
    assert (reply.content, reply.finish_reason, reply.model, reply.attempts) == ("Plan ready.", "stop", "test-model", 1)
    assert (reply.tokens.prompt, reply.tokens.completion) == (10, 5)
    assert reply.latency_ms >= 0


async def test_tool_calls_parsed_and_malformed_kept() -> None:
    calls = (
        ("p1", "ocr_document", '{"file_path": "/a.pdf"}'),
        ("p2", "read", "{bad json"),
        ("p3", "read", "[1, 2]"),
        ("p4", "finish", ""),
        ("p5", "", "{}"),
        ("p6", None, "{}"),
    )
    llm, _ = client(FakeProvider(response(None, calls, finish="tool_calls")))
    reply = await llm.complete(USER, TOOLS)
    parsed = {call.id: call for call in reply.tool_calls}
    assert parsed["p1"].arguments == {"file_path": "/a.pdf"}
    assert "not valid JSON" in parsed["p2"].parse_error
    assert parsed["p3"].parse_error == "arguments must be a JSON object."
    assert parsed["p4"].arguments == {}
    assert parsed["p5"].name == MISSING_TOOL_NAME and parsed["p5"].parse_error
    assert parsed["p6"].name == MISSING_TOOL_NAME
    assert reply.finish_reason == "tool_calls"


async def test_missing_and_duplicate_ids_made_unique() -> None:
    calls = (("call_1_1", "read", "{}"), (None, "read", "{}"), ("call_1_1", "read", "{}"), ("", "read", "{}"))
    llm, _ = client(FakeProvider(response(None, calls, finish="tool_calls")))
    reply = await llm.complete(USER, TOOLS)
    ids = [call.id for call in reply.tool_calls]
    assert ids[0] == "call_1_1"
    assert len(set(ids)) == 4


async def test_generated_ids_change_per_turn() -> None:
    llm, _ = client(FakeProvider(response(None, ((None, "read", "{}"),)), response(None, ((None, "read", "{}"),))))
    first = await llm.complete(USER, TOOLS)
    second = await llm.complete(USER, TOOLS)
    assert first.tool_calls[0].id != second.tool_calls[0].id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("stop", "stop"),
        ("end_turn", "stop"),
        ("tool_calls", "tool_calls"),
        ("tool_use", "tool_calls"),
        ("length", "length"),
        ("MAX_TOKENS", "length"),
        ("content_filter", "content_filter"),
        ("function_call", "other"),
        ("weird", "other"),
        (None, "other"),
    ],
)
async def test_finish_reasons_normalized(raw: str | None, expected: str) -> None:
    llm, _ = client(FakeProvider(response("text", finish=raw)))
    reply = await llm.complete(USER)
    assert reply.finish_reason == expected
    assert reply.truncated is (expected == "length")


async def test_missing_usage_is_unknown_not_zero() -> None:
    llm, _ = client(FakeProvider(response("ok", usage=None)))
    reply = await llm.complete(USER)
    assert reply.tokens.prompt is None and reply.tokens.total is None


async def test_partial_usage_kept() -> None:
    llm, _ = client(FakeProvider(response("ok", usage=NS(prompt_tokens=7, completion_tokens=None))))
    reply = await llm.complete(USER)
    assert (reply.tokens.prompt, reply.tokens.completion) == (7, None)


async def test_retry_on_timeout_rate_limit_and_server_error() -> None:
    provider = FakeProvider(
        asyncio.TimeoutError(),
        api_error(openai.RateLimitError, 429, "slow down"),
        response("done"),
    )
    llm, sleeps = client(provider)
    reply = await llm.complete(USER)
    assert reply.content == "done" and reply.attempts == 3
    assert sleeps.calls == [1.0, 2.0]


async def test_retry_on_generic_5xx() -> None:
    llm, _ = client(FakeProvider(api_error(openai.APIStatusError, 503, "unavailable"), response("ok")))
    assert (await llm.complete(USER)).attempts == 2


async def test_retries_exhausted_raise_last_error() -> None:
    llm, sleeps = client(FakeProvider(asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError()))
    with pytest.raises(LlmError) as error:
        await llm.complete(USER)
    assert error.value.code == "llm_timeout" and error.value.retryable is True
    assert len(sleeps.calls) == 2


async def test_empty_reply_retried_then_bad_reply_error() -> None:
    llm, _ = client(FakeProvider(response("  "), response(None), response(choices=False)))
    with pytest.raises(LlmError) as error:
        await llm.complete(USER)
    assert error.value.code == "llm_bad_reply"


async def test_empty_reply_recovers() -> None:
    llm, _ = client(FakeProvider(response(""), response("ok")))
    assert (await llm.complete(USER)).content == "ok"


async def test_rejected_temperature_dropped_and_remembered() -> None:
    provider = FakeProvider(
        api_error(openai.BadRequestError, 400, "Unsupported parameter: 'temperature'"),
        response("one"),
        response("two"),
    )
    llm, _ = client(provider)
    first = await llm.complete(USER)
    await llm.complete(USER)
    assert first.attempts == 1 and first.dropped_params == ["temperature"]
    assert "temperature" not in provider.calls[1] and "temperature" not in provider.calls[2]


async def test_max_tokens_renamed_before_dropping() -> None:
    provider = FakeProvider(
        api_error(openai.BadRequestError, 400, "'max_tokens' is not supported, use 'max_completion_tokens'"),
        response("ok"),
    )
    llm, _ = client(provider)
    reply = await llm.complete(USER)
    assert provider.calls[1]["max_completion_tokens"] == 4096 and "max_tokens" not in provider.calls[1]
    assert reply.dropped_params == []


async def test_max_tokens_dropped_only_as_last_resort_and_recorded() -> None:
    provider = FakeProvider(api_error(openai.BadRequestError, 400, "max_tokens is not allowed"), response("ok"))
    llm, _ = client(provider)
    reply = await llm.complete(USER)
    assert "max_tokens" not in provider.calls[1]
    assert reply.dropped_params == ["max_tokens"]


async def test_auto_tool_choice_may_be_dropped() -> None:
    provider = FakeProvider(api_error(openai.BadRequestError, 400, "tool_choice is not supported"), response("ok"))
    llm, _ = client(provider)
    reply = await llm.complete(USER, TOOLS)
    assert "tool_choice" not in provider.calls[1] and reply.dropped_params == ["tool_choice"]


async def test_required_tool_choice_never_dropped() -> None:
    provider = FakeProvider(api_error(openai.BadRequestError, 400, "tool_choice 'required' is not supported"))
    llm, _ = client(provider)
    with pytest.raises(LlmError) as error:
        await llm.complete(USER, TOOLS, tool_choice="required")
    assert error.value.code == "llm_unavailable"
    assert len(provider.calls) == 1


async def test_tool_calling_rejected_is_unavailable() -> None:
    provider = FakeProvider(api_error(openai.BadRequestError, 400, "This model does not support tools"))
    llm, _ = client(provider)
    with pytest.raises(LlmError) as error:
        await llm.complete(USER, TOOLS)
    assert error.value.code == "llm_unavailable"


async def test_other_bad_request_is_not_retried() -> None:
    provider = FakeProvider(api_error(openai.BadRequestError, 400, "context length exceeded"))
    llm, sleeps = client(provider)
    with pytest.raises(LlmError) as error:
        await llm.complete(USER)
    assert error.value.code == "llm_error" and sleeps.calls == []


@pytest.mark.parametrize(
    ("cls", "status"),
    [(openai.AuthenticationError, 401), (openai.PermissionDeniedError, 403), (openai.NotFoundError, 404)],
)
async def test_access_errors_are_unavailable_and_scrubbed(cls: type[openai.APIStatusError], status: int) -> None:
    llm, _ = client(FakeProvider(api_error(cls, status, f"key {SECRET} rejected")))
    with pytest.raises(LlmError) as error:
        await llm.complete(USER)
    assert error.value.code == "llm_unavailable"
    assert SECRET not in error.value.message and "[secret]" in error.value.message


async def test_other_4xx_not_retried() -> None:
    llm, sleeps = client(FakeProvider(api_error(openai.APIStatusError, 409, "conflict")))
    with pytest.raises(LlmError) as error:
        await llm.complete(USER)
    assert error.value.code == "llm_error" and sleeps.calls == []


async def test_no_time_left_fails_without_calling() -> None:
    provider = FakeProvider()
    llm, _ = client(provider)
    with pytest.raises(LlmError) as error:
        await llm.complete(USER, deadline=monotonic() - 1)
    assert error.value.code == "llm_timeout"
    assert provider.calls == []


async def test_timeout_capped_by_deadline() -> None:
    provider = FakeProvider(response("ok"))
    llm, _ = client(provider, LlmSettings(base_url="http://x", model="m", api_key="k", timeout_s=90))
    await llm.complete(USER, deadline=monotonic() + 5)
    assert provider.calls[0]["timeout"] <= 5


async def test_hanging_provider_times_out() -> None:
    async def hang() -> Any:
        await asyncio.sleep(10)

    settings = LlmSettings(base_url="http://x", model="m", api_key="k", timeout_s=1, max_retries=0)
    llm, _ = client(FakeProvider(hang), settings)
    with pytest.raises(LlmError) as error:
        await llm.complete(USER, deadline=monotonic() + 0.05)
    assert error.value.code == "llm_timeout"


async def test_backoff_capped_by_deadline() -> None:
    llm, sleeps = client(FakeProvider(asyncio.TimeoutError(), response("ok")))
    await llm.complete(USER, deadline=monotonic() + 0.5)
    assert sleeps.calls and sleeps.calls[0] <= 0.5


async def test_aclose_closes_provider() -> None:
    provider = FakeProvider()
    llm, _ = client(provider)
    await llm.aclose()
    assert provider.closed is True


def test_default_client_disables_sdk_retries() -> None:
    llm = LlmClient(SETTINGS)
    assert llm._client.max_retries == 0
    assert str(llm._client.base_url).startswith("http://llm.test/v1")
