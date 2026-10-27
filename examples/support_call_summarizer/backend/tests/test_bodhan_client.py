import json

import httpx
import pytest
import respx

from app import bodhan_client
from app.bodhan_client import BodhanClient, BodhanError

BASE = "https://api.bodhan.ai"
STT = f"{BASE}/v1/audio/transcriptions"
MT = f"{BASE}/v1/chat/completions"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(bodhan_client.asyncio, "sleep", fake_sleep)
    return slept


def err(status, code):
    return httpx.Response(status, json={"error": {"message": code, "code": code, "request_id": "r1"}})


def chat(content, finish_reason="stop"):
    return httpx.Response(
        200,
        json={"id": "x", "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"content": content}}]},
    )


@pytest.fixture
async def client():
    c = BodhanClient("stt-key-placeholder", "mt-key-placeholder", max_retries=3)
    yield c
    await c.aclose()


@respx.mock
async def test_transcribe_sends_model_language_and_auth(client):
    route = respx.post(STT).mock(return_value=httpx.Response(200, json={"text": " नमस्ते "}))
    assert await client.transcribe(b"RIFF....", "hi") == "नमस्ते"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer stt-key-placeholder"
    body = request.content.decode("latin-1")
    assert 'name="model"' in body and "indic-transcribe" in body
    assert 'name="language"' in body and "\r\n\r\nhi\r\n" in body
    assert 'filename="piece.wav"' in body


@respx.mock
async def test_transcribe_without_language_lets_bodhan_detect(client):
    route = respx.post(STT).mock(return_value=httpx.Response(200, json={"text": "hello"}))
    await client.transcribe(b"RIFF", None)
    assert 'name="language"' not in route.calls.last.request.content.decode("latin-1")


@respx.mock
async def test_translate_uses_chat_transport_and_its_own_key(client):
    route = respx.post(MT).mock(return_value=chat(" Hello "))
    result = await client.translate("नमस्ते")
    assert result.text == "Hello" and result.truncated is False
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer mt-key-placeholder"
    assert json.loads(request.content) == {
        "model": "indic-translate",
        "target_language": "en",
        "messages": [{"role": "user", "content": "नमस्ते"}],
    }


@respx.mock
async def test_translate_cut_off_by_token_limit_is_truncated(client):
    respx.post(MT).mock(return_value=chat("Hello my inter", finish_reason="length"))
    assert (await client.translate("x")).truncated is True


@respx.mock
async def test_translate_unexpected_body(client):
    respx.post(MT).mock(return_value=httpx.Response(200, json={"choices": []}))
    with pytest.raises(BodhanError) as e:
        await client.translate("x")
    assert e.value.code == "unexpected_response"


async def test_one_key_serves_both_models_when_only_one_is_given():
    c = BodhanClient("shared-key-placeholder")
    with respx.mock:
        route = respx.post(MT).mock(return_value=chat("ok"))
        await c.translate("x")
    assert route.calls.last.request.headers["authorization"] == "Bearer shared-key-placeholder"
    await c.aclose()


@respx.mock
async def test_rate_limit_is_retried_with_backoff(client, no_sleep):
    route = respx.post(MT).mock(
        side_effect=[
            err(429, "rate_limit_exceeded_error"),
            httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"code": "rate_limit_exceeded_error"}}),
            chat("ok"),
        ]
    )
    assert (await client.translate("x")).text == "ok"
    assert route.call_count == 3
    assert no_sleep[1] == 3.0, "Retry-After must be honoured"


@respx.mock
async def test_server_errors_give_up_after_max_retries(client):
    route = respx.post(STT).mock(return_value=err(503, "internal_error"))
    with pytest.raises(BodhanError) as e:
        await client.transcribe(b"RIFF")
    assert e.value.code == "internal_error" and e.value.request_id == "r1"
    assert route.call_count == 4  # first try + 3 retries


@respx.mock
async def test_network_errors_are_retried(client):
    respx.post(STT).mock(side_effect=[httpx.ConnectError("down"), httpx.Response(200, json={"text": "hi"})])
    assert await client.transcribe(b"RIFF") == "hi"


@respx.mock
async def test_client_errors_are_not_retried(client):
    route = respx.post(MT).mock(return_value=err(400, "unsupported_language"))
    with pytest.raises(BodhanError) as e:
        await client.translate("x")
    assert e.value.code == "unsupported_language"
    assert route.call_count == 1


@respx.mock
async def test_auth_error_retried_once_only_after_key_worked(client):
    route = respx.post(MT).mock(return_value=err(401, "invalid_api_key_error"))
    with pytest.raises(BodhanError) as e:
        await client.translate("x")
    assert e.value.is_auth_error and route.call_count == 1

    route.mock(side_effect=[chat("a"), err(401, "invalid_api_key_error"), chat("b")])
    await client.translate("x")
    assert (await client.translate("y")).text == "b"


@respx.mock
async def test_415_without_error_body(client):
    respx.post(STT).mock(return_value=httpx.Response(415, text="unsupported"))
    with pytest.raises(BodhanError) as e:
        await client.transcribe(b"RIFF")
    assert e.value.code == "unsupported_media_type"


async def test_missing_key_fails_fast():
    c = BodhanClient("")
    with pytest.raises(BodhanError) as e:
        await c.transcribe(b"RIFF")
    assert e.value.code == "missing_api_key" and e.value.is_auth_error
    await c.aclose()
