"""ClaudeClient request mapping and error handling, with a stand-in for the Anthropic SDK client."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.claude_client import FALLBACK_BETA, ClaudeClient
from app.llm import LLMError

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
FORMAT = {"type": "json_schema", "json_schema": {"name": "x", "schema": SCHEMA, "strict": True}}
MESSAGES = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]


def block(type_, text=""):
    return SimpleNamespace(type=type_, text=text)


def message(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(
        model="claude-opus-5-5",
        stop_reason=stop_reason,
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        _request_id="req_test",
    )


class FakeSDK:
    def __init__(self, result):
        self.result = result
        self.kwargs = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def close(self):
        pass


def client(result, **kw):
    sdk = FakeSDK(result)
    return ClaudeClient("sk-ant-placeholder", client=sdk, **kw), sdk


def status_error(status, type_, msg):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, headers={"request-id": "req_err"})
    return anthropic.APIStatusError(msg, response=response, body={"error": {"type": type_, "message": msg}})


async def test_maps_system_schema_effort_and_fallbacks():
    c, sdk = client(message(block("thinking"), block("text", ' {"a": "b"} ')))
    result = await c.chat(MESSAGES, response_format=FORMAT)
    assert result.content == '{"a": "b"}' and result.truncated is False
    kw = sdk.kwargs
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == "be brief"
    assert kw["messages"] == [{"role": "user", "content": "hi"}]
    assert kw["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert "temperature" not in kw and "thinking" not in kw


async def test_only_text_after_a_server_side_fallback_counts():
    c, _ = client(message(block("text", "partial"), block("fallback"), block("text", "{}")))
    assert (await c.chat(MESSAGES)).content == "{}"


async def test_max_tokens_is_truncated():
    c, _ = client(message(block("text", '{"a'), stop_reason="max_tokens"))
    assert (await c.chat(MESSAGES)).truncated is True


async def test_refusal_is_an_error():
    c, _ = client(message(stop_reason="refusal"))
    with pytest.raises(LLMError) as e:
        await c.chat(MESSAGES)
    assert e.value.code == "refusal"


@pytest.mark.parametrize(
    ("status", "type_", "msg", "check"),
    [
        (401, "authentication_error", "invalid x-api-key", "is_auth_error"),
        (400, "invalid_request_error", "Your credit balance is too low to access the API.", "is_out_of_credit"),
    ],
)
async def test_status_errors_are_classified(status, type_, msg, check):
    c, _ = client(status_error(status, type_, msg))
    with pytest.raises(LLMError) as e:
        await c.chat(MESSAGES)
    assert getattr(e.value, check) and e.value.status == status


async def test_missing_key():
    c = ClaudeClient("", client=FakeSDK(None))
    with pytest.raises(LLMError) as e:
        await c.chat(MESSAGES)
    assert e.value.is_auth_error
