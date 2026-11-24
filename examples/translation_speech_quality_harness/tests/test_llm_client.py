import httpx
import pytest
from bodhan_eval.cache import Cache
from bodhan_eval.client.llm import LLMClient


@pytest.mark.asyncio
async def test_llm_client_does_not_cache_unvalidated_raw_replies(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"findings": []}'}}]},
        )

    async with LLMClient(
        "https://llm.example.invalid/v1",
        "mock-model",
        "test-key",
        Cache(tmp_path),
        transport=httpx.MockTransport(handler),
    ) as client:
        first = await client.chat("system", "user")
        second = await client.chat("system", "user")

    assert first == second == '{"findings": []}'
    assert calls == 2
