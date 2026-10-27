import json

import httpx
import pytest
import respx

from app import sarvam_client
from app.sarvam_client import SarvamClient, SarvamError

CHAT = "https://api.sarvam.ai/v1/chat/completions"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(sarvam_client.asyncio, "sleep", fake_sleep)
    return slept


def err(status, code):
    return httpx.Response(status, json={"error": {"message": code, "code": code, "request_id": "r1"}})


def chat(content, finish_reason="stop"):
    return httpx.Response(
        200,
        json={
            "id": "x",
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


@pytest.fixture
async def client():
    c = SarvamClient("sarvam-key-placeholder", max_retries=3)
    yield c
    await c.aclose()


@respx.mock
async def test_chat_sends_model_key_and_options(client):
    route = respx.post(CHAT).mock(return_value=chat(' {"ok": true} '))
    fmt = {"type": "json_object"}
    result = await client.chat([{"role": "user", "content": "hi"}], response_format=fmt)
    assert result.content == '{"ok": true}' and result.truncated is False
    request = route.calls.last.request
    assert request.headers["api-subscription-key"] == "sarvam-key-placeholder"
    body = json.loads(request.content)
    assert body["model"] == "sarvam-105b"
    assert body["reasoning_effort"] == "low" and body["max_tokens"] == 4096
    assert body["response_format"] == fmt


@respx.mock
async def test_reasoning_none_is_sent_as_null():
    route = respx.post(CHAT).mock(return_value=chat("x"))
    c = SarvamClient("k", reasoning_effort="none")
    await c.chat([{"role": "user", "content": "hi"}])
    await c.aclose()
    assert json.loads(route.calls.last.request.content)["reasoning_effort"] is None


@respx.mock
async def test_length_finish_is_truncated(client):
    respx.post(CHAT).mock(return_value=chat('{"summ', finish_reason="length"))
    assert (await client.chat([])).truncated is True


@respx.mock
async def test_rate_limit_and_server_errors_are_retried(client, no_sleep):
    route = respx.post(CHAT).mock(
        side_effect=[err(429, "rate_limit_exceeded_error"), err(503, "internal_server_error"), chat("ok")]
    )
    assert (await client.chat([])).content == "ok"
    assert route.call_count == 3 and len(no_sleep) == 2


@respx.mock
async def test_rejected_key_is_not_retried(client):
    route = respx.post(CHAT).mock(return_value=err(403, "invalid_api_key_error"))
    with pytest.raises(SarvamError) as e:
        await client.chat([])
    assert e.value.is_auth_error and route.call_count == 1


async def test_missing_key_fails_without_a_request():
    c = SarvamClient("")
    with pytest.raises(SarvamError) as e:
        await c.chat([])
    await c.aclose()
    assert e.value.code == "missing_api_key" and e.value.is_auth_error


@respx.mock
async def test_unexpected_body(client):
    respx.post(CHAT).mock(return_value=httpx.Response(200, json={"choices": []}))
    with pytest.raises(SarvamError) as e:
        await client.chat([])
    assert e.value.code == "unexpected_response"
