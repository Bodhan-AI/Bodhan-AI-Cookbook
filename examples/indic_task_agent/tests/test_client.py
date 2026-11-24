"""Retries, error mapping, caching and request de-duplication in the Bodhan client."""

import asyncio

import httpx
import pytest

from bodhan_mcp.client import TRANSLATE, ToolFailure
from conftest import BASE, chat_json


async def _chat(services, text="hello"):
    return await services.client.chat(TRANSLATE, text, {"target_language_code": "hi"}, text.encode())


async def test_retries_503_then_succeeds(services, respx_mock):
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(side_effect=[
        httpx.Response(503, json={"error": {"message": "busy"}}), httpx.Response(200, json=chat_json("ok"))])
    res = await _chat(services)
    assert res.content == "ok" and route.call_count == 2


async def test_does_not_retry_400(services, respx_mock):
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(
        400, json={"error": {"message": "unsupported language 'fr'"}}))
    with pytest.raises(ToolFailure) as exc:
        await _chat(services)
    assert exc.value.code == "UPSTREAM_REJECTED" and route.call_count == 1


async def test_500_is_retried_only_once(services, respx_mock):
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(500, json={"error": {"message": "x"}}))
    with pytest.raises(ToolFailure) as exc:
        await _chat(services)
    assert exc.value.code == "UPSTREAM_ERROR" and route.call_count == 2


async def test_auth_error_and_redaction(services, respx_mock):
    fake_key = "sk-" + "abcdefghijklmnop12345"  # built at runtime so secret scanners don't flag the test
    respx_mock.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(
        401, json={"error": {"message": f"Invalid token. Received API Key = {fake_key}"}}))
    with pytest.raises(ToolFailure) as exc:
        await _chat(services)
    assert exc.value.code == "AUTH_FAILED" and fake_key not in exc.value.message


async def test_out_of_credit_429_is_not_retried(services, respx_mock):
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(
        429, json={"error": {"message": "Insufficient credit balance"}}))
    with pytest.raises(ToolFailure) as exc:
        await _chat(services)
    assert exc.value.code == "OUT_OF_CREDIT" and route.call_count == 1


async def test_rate_limit_429_is_retried(services, respx_mock):
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(side_effect=[
        httpx.Response(429, json={"error": {"message": "slow down"}}), httpx.Response(200, json=chat_json("ok"))])
    assert (await _chat(services)).content == "ok" and route.call_count == 2


async def test_rate_limit_gets_more_attempts_and_a_shared_cooldown(services, respx_mock):
    limited = httpx.Response(429, json={"error": {"message": "Maximum rate limit reached for the model"}})
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(return_value=limited)
    with pytest.raises(ToolFailure) as exc:
        await _chat(services)
    assert exc.value.code == "RATE_LIMITED"
    assert route.call_count == services.settings.rate_limit_attempts > services.settings.max_attempts
    assert services.client._cooldown.get(TRANSLATE)  # other requests to this model now wait too


async def test_null_body_is_an_error(services, respx_mock):
    respx_mock.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(200, json=None))
    with pytest.raises(ToolFailure) as exc:
        await _chat(services)
    assert exc.value.code == "EMPTY_RESPONSE"


async def test_timeouts_are_retried_then_reported(services, respx_mock):
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ToolFailure) as exc:
        await _chat(services)
    assert exc.value.code == "TIMEOUT" and route.call_count == services.settings.max_attempts


async def test_same_input_is_sent_once(services, respx_mock):
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(200, json=chat_json("ok")))
    first = await _chat(services)
    second = await _chat(services)
    assert route.call_count == 1 and not first.cached and second.cached


async def test_concurrent_identical_requests_share_one_call(services, respx_mock):
    async def slow(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=chat_json("ok"))

    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(side_effect=slow)
    results = await asyncio.gather(*(_chat(services) for _ in range(5)))
    assert route.call_count == 1 and all(r.content == "ok" for r in results)


async def test_missing_key_is_reported(tmp_path):
    from bodhan_mcp.tools import Services
    from conftest import make_settings

    svc = Services.create(make_settings(tmp_path, keys={}), http=httpx.AsyncClient())
    with pytest.raises(ToolFailure) as exc:
        await _chat(svc)
    assert exc.value.code == "MISSING_API_KEY" and "BODHAN_TRANSLATE_KEY" in exc.value.hint
