"""Test budget and response handling at the HTTP boundary without paid calls."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from bodhan_eval.providers import CallError, Providers, retry_delay
from bodhan_eval.workflow import request_judgment


def test_translation_uses_hosted_chat_contract(tmp_path, monkeypatch):
    """Check direction, actual token cost, and rejection of a truncated translation."""
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "local-test")
    finish_reasons = iter(("stop", "length"))

    def receive(request):
        """Exercise the request and response contract observed on the hosted route."""
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content) == {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": "कक्षा नौ बजे शुरू होती है।"}],
            "source_language_code": "hi",
            "target_language_code": "en",
            "max_output_tokens": 128,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": next(finish_reasons), "message": {"content": "Class starts at nine."}}],
                "usage": {"completion_tokens": 5},
            },
        )

    async def check():
        """Send the same input against independent caches so both responses are decoded."""
        for cache_name in ("complete.sqlite", "truncated.sqlite"):
            async with Providers(tmp_path / cache_name, 10_000, 1) as provider:
                await provider.http.aclose()
                provider.http = httpx.AsyncClient(transport=httpx.MockTransport(receive))
                if cache_name == "truncated.sqlite":
                    with pytest.raises(CallError, match="output token limit"):
                        await provider.translate("कक्षा नौ बजे शुरू होती है।", "en", source_lang="hi")
                else:
                    result = await provider.translate("कक्षा नौ बजे शुरू होती है।", "en", source_lang="hi")
                    assert result.value.decode() == "Class starts at nine."
                    assert result.usage["micro_inr"] == 100

    asyncio.run(check())


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
