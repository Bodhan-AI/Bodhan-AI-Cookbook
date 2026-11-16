"""Test budget and response handling at the HTTP boundary without paid calls."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from bodhan_eval.providers import CallError, Providers, retry_delay
from bodhan_eval.workflow import request_judgment


def test_unusable_judge_verdict_does_not_poison_cache(tmp_path, monkeypatch):
    """Reject a malformed verdict, then verify that a valid receipt is replayed from SQLite."""
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_MODEL", "local-test")
    monkeypatch.setenv("LLM_API_KEY", "local-test")
    receipts = iter(["no JSON verdict", '{"findings":[],"verify_speech":false}'])

    def receive(request):
        """Deliver controlled protocol bodies through the real client and cache logic."""
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": next(receipts)}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    async def check():
        """Verify only two dispatches even though three requests are evaluated."""
        async with Providers(tmp_path / "cache.sqlite", 0, 1) as provider:
            await provider.http.aclose()
            provider.http = httpx.AsyncClient(transport=httpx.MockTransport(receive))
            with pytest.raises(CallError, match="invalid JSON"):
                await request_judgment(provider, "{}", {})
            fresh, verdict = await request_judgment(provider, "{}", {})
            cached, replay = await request_judgment(provider, "{}", {})
            assert fresh == cached
            assert verdict == replay == ([], False, 0)
            assert provider.telemetry["requests"] == 2
            assert provider.telemetry["cache_hits"] == 1
            assert provider.telemetry["judge_output_tokens"] == 2

    asyncio.run(check())


def test_budget_refuses_dispatch_and_retry_after_respects_date(tmp_path, monkeypatch):
    """Enforce the request cap before dispatch and interpret both Retry-After formats."""
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "local-test")

    async def check():
        """A fresh call without available Bodhan credit must never open a connection."""
        async with Providers(tmp_path / "cache.sqlite", 0, 1) as provider:
            with pytest.raises(CallError, match="spend cap"):
                await provider.translate("Read the first sentence.", "hi")
            assert provider.telemetry["requests"] == 0

    asyncio.run(check())
    now = datetime(2026, 11, 17, tzinfo=UTC)
    assert retry_delay("Tue, 17 Nov 2026 00:00:20 GMT", 0, now) == 20
    assert retry_delay("17", 0, now) == 17
    assert retry_delay("unusable header", 2, now) == 4
