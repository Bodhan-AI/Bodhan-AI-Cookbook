"""Test budget and response handling at the HTTP boundary without paid calls."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from bodhan_eval.providers import ATTEMPTS, CallError, Providers, retry_delay
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
    """Resample a malformed verdict, cache only the valid one, and give up after bounded attempts."""
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_MODEL", "local-test")
    monkeypatch.setenv("LLM_API_KEY", "local-test")
    receipts = iter(["no JSON verdict", '{"findings":[],"verify_speech":false}'] + ["still no verdict"] * 10)

    def receive(request):
        """Deliver controlled protocol bodies through the real client and cache logic."""
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["temperature"] == 0
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": next(receipts)}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    async def check():
        """The first verdict is resampled in place. A replay is served from SQLite."""
        async with Providers(tmp_path / "cache.sqlite", 0, 1) as provider:
            await provider.http.aclose()
            provider.http = httpx.AsyncClient(transport=httpx.MockTransport(receive))
            fresh, verdict = await request_judgment(provider, "{}", {})
            cached, replay = await request_judgment(provider, "{}", {})
            assert fresh == cached
            assert verdict == replay == ([], False, 0)
            assert provider.telemetry["requests"] == 2
            assert provider.telemetry["judge_invalid_retries"] == 1
            assert provider.telemetry["cache_hits"] == 1
            with pytest.raises(CallError, match="findings schema"):
                await request_judgment(provider, '{"other": 1}', {})
            assert provider.telemetry["requests"] == 2 + ATTEMPTS

    asyncio.run(check())


def test_judge_falls_back_when_json_mode_is_rejected(tmp_path, monkeypatch):
    """An endpoint that rejects optional fields still receives a plain request."""
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_MODEL", "local-test")
    monkeypatch.setenv("LLM_API_KEY", "local-test")
    seen = []

    def receive(request):
        """Reject JSON mode once, then accept the plain Chat Completions body."""
        body = json.loads(request.content)
        seen.append(sorted(body))
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"findings":[]}'}}]})

    async def check():
        async with Providers(tmp_path / "cache.sqlite", 0, 1) as provider:
            await provider.http.aclose()
            provider.http = httpx.AsyncClient(transport=httpx.MockTransport(receive))
            _, verdict = await request_judgment(provider, "{}", {})
            assert verdict == ([], False, 0)
            assert seen == [["messages", "model", "response_format", "temperature"], ["messages", "model"]]

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


def test_long_quota_window_fails_fast_for_later_calls(tmp_path, monkeypatch):
    """A daily-quota 429 must not make every other row sleep until the quota reopens."""
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_MODEL", "local-test")
    monkeypatch.setenv("LLM_API_KEY", "local-test")

    def receive(request):
        return httpx.Response(429, headers={"retry-after": "600"}, json={"error": {"message": "tokens per day"}})

    async def check():
        async with Providers(tmp_path / "cache.sqlite", 0, 1) as provider:
            await provider.http.aclose()
            provider.http = httpx.AsyncClient(transport=httpx.MockTransport(receive))
            with pytest.raises(CallError, match=r"retry after \d+ s"):
                await request_judgment(provider, "{}", {})
            with pytest.raises(CallError, match="rate limited for another"):
                await request_judgment(provider, '{"other": 1}', {})
            assert provider.telemetry["requests"] == 1

    asyncio.run(check())
