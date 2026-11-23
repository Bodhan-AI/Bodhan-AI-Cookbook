import json

import httpx
import pytest
from bodhan_eval.budget import Budget
from bodhan_eval.cache import Cache
from bodhan_eval.client.bodhan import BodhanClient
from bodhan_eval.client.errors import BudgetExhausted


async def _async_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_translate_cache_hit_zero_http(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "indic-translate"
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        assert body["target_language_code"] == "hi"
        assert body["target_script"] == "native"
        calls += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "नमस्ते"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    async with BodhanClient(
        "test-key",
        "test-key",
        "test-key",
        Cache(tmp_path),
        Budget(10),
        transport=httpx.MockTransport(handler),
    ) as client:
        first = await client.translate("Hello", "hi")
        second = await client.translate("Hello", "hi")
    assert first.text == second.text == "नमस्ते"
    assert calls == 1


@pytest.mark.asyncio
async def test_translate_retries_credit_out_terminal(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={"retry-after": "1"},
            json={"error": {"message": "credit out of balance", "code": "rate_limit_exceeded_error", "request_id": "r1"}},
        )

    async with BodhanClient(
        "test-key", "test-key", "test-key", Cache(tmp_path), Budget(10),
        transport=httpx.MockTransport(handler),
        sleep=_async_sleep,
    ) as client:
        with pytest.raises(BudgetExhausted):
            await client.translate("Hello", "hi")
    assert calls == 1


@pytest.mark.asyncio
async def test_translate_retries_5xx_then_succeeds(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": {"message": "busy", "code": "internal_error", "request_id": "r1"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "सफल"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"completion_tokens": 1},
            },
        )

    async with BodhanClient(
        "test-key", "test-key", "test-key", Cache(tmp_path), Budget(10),
        transport=httpx.MockTransport(handler), sleep=_async_sleep,
    ) as client:
        result = await client.translate("Hello", "hi")
    assert result.text == "सफल"
    assert calls == 2


@pytest.mark.asyncio
async def test_translate_maps_openai_length_finish_reason_to_truncated(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "नमस्ते"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"completion_tokens": 64},
            },
        )

    async with BodhanClient(
        "test-key",
        "test-key",
        "test-key",
        Cache(tmp_path),
        Budget(10),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.translate("Hello", "hi", max_output_tokens=64)

    assert result.text == "नमस्ते"
    assert result.truncated is True
    assert result.usage["completion_tokens"] == 64
