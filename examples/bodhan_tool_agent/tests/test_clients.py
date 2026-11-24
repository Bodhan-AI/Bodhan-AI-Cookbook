"""Tests for base HTTP client, retries, semaphore, cache, and Bodhan wrappers (Slice 3)."""

import httpx
import pytest

from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient


@pytest.mark.asyncio
async def test_client_retry_on_429():
    """Verify exponential retry on HTTP 429 rate limit."""
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, json={"error": "Rate limit exceeded"})
        return httpx.Response(200, json={"result": "success", "text": "transcribed"})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    # Inject mock transport client
    base_client._client = httpx.AsyncClient(transport=transport)

    res = await base_client.call(
        model="indic-transcribe",
        endpoint="/audio/transcriptions",
        payload={"dummy": "data"},
        cost_metrics={"duration_s": 60.0},
    )

    assert attempts == 3
    assert res["data"]["result"] == "success"
    assert res["cost_inr"] == 0.10
    await base_client.close()


@pytest.mark.asyncio
async def test_client_exhausts_retries_on_500():
    """Verify failure after 3 attempts on server errors."""
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"error": "Internal Server Error"})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)

    with pytest.raises(httpx.HTTPStatusError):
        await base_client.call(
            model="indic-translate",
            endpoint="/chat/completions",
            payload={"text": "hello"},
        )

    assert attempts == 3
    await base_client.close()


@pytest.mark.asyncio
async def test_client_cache_hit_and_zero_cost():
    """Verify Constraint C10: Identical calls hit cache and incur zero cost."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"translated": "வணக்கம்"})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)

    payload = {"text": "Hello", "source_lang": "en", "target_lang": "ta"}
    # Call 1: Cache miss
    res1 = await base_client.call(
        model="indic-translate",
        endpoint="/chat/completions",
        payload=payload,
        cost_metrics={"output_tokens": 1000},
    )
    assert call_count == 1
    assert res1["cached"] is False
    assert res1["cost_inr"] == 0.02

    # Call 2: Cache hit
    res2 = await base_client.call(
        model="indic-translate",
        endpoint="/chat/completions",
        payload=payload,
        cost_metrics={"output_tokens": 1000},
    )
    assert call_count == 1  # No additional network call made
    assert res2["cached"] is True
    assert res2["cost_inr"] == 0.0  # Zero cost for cache hit
    await base_client.close()


def test_cost_calculation():
    """Verify cost calculation across all 4 models."""
    client = BaseBodhanClient()
    # Transcribe: 120s = 2 min * 0.10 = 0.20
    assert client.calculate_cost("indic-transcribe", {"duration_s": 120.0}) == 0.20
    # Translate: 50,000 tokens * (0.20 / 10,000) = 1.00
    assert client.calculate_cost("indic-translate", {"output_tokens": 50000}) == 1.00
    # Speak: 10,000 chars * (6.00 / 10,000) = 6.00
    assert client.calculate_cost("indic-speak", {"chars": 10000}) == 6.00
    # OCR: 3 pages * 0.20 = 0.60
    assert client.calculate_cost("indic-ocr", {"pages": 3}) == 0.60


@pytest.mark.asyncio
async def test_bodhan_client_wrappers():
    """Verify high-level BodhanClient model wrappers."""
    endpoints_called = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        endpoints_called.append(request.url.path)
        return httpx.Response(200, json={"status": "ok", "mocked": True})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    await bodhan.transcribe(b"dummy_wav", duration_s=10.0)
    assert any("/audio/transcriptions" in ep for ep in endpoints_called)

    await bodhan.translate("Hello", "en", "hi")
    assert any("/chat/completions" in ep for ep in endpoints_called)

    await bodhan.speak("नमस्ते", "hi", "hi_female_1")
    assert any("/audio/speech" in ep for ep in endpoints_called)

    await bodhan.ocr(b"dummy_png")
    assert any("/chat/completions" in ep or "/ocr" in ep for ep in endpoints_called)

    await base_client.close()
