"""Unit tests for BodhanClient: caching, dedupe, cost math, retries (429/5xx/401/4xx), and key fallback."""

from __future__ import annotations

from pathlib import Path

import pytest
from bodhan_mcp.cache import DiskCache
from bodhan_mcp.client import BodhanAPIError, BodhanClient
from bodhan_mcp.pricing import cost_ocr, cost_speak, cost_transcribe, cost_translate

from tests.fakes import FakeBodhanState, make_fake_bodhan_transport, make_sine_wav_bytes


def test_pricing_math_matches_brief() -> None:
    assert cost_transcribe(60.0) == pytest.approx(0.10)
    assert cost_transcribe(30.0) == pytest.approx(0.05)
    assert cost_translate(10_000) == pytest.approx(0.20)
    assert cost_speak(10_000) == pytest.approx(6.00)
    assert cost_ocr(8) == pytest.approx(1.60)


async def test_cache_dedupes_identical_calls_at_zero_cost(tmp_path: Path) -> None:
    transport, state = make_fake_bodhan_transport()
    client = BodhanClient(
        cache=DiskCache(tmp_path / "cache"),
        transport=transport,
        base_backoff_s=0.005,
    )
    try:
        r1 = await client.translate("Hello world", target_language="ta")
        r2 = await client.translate("Hello world", target_language="ta")
        assert r1.cache_hit is False
        assert r1.cost_inr > 0
        assert r2.cache_hit is True
        assert r2.cost_inr == 0.0
        assert r1.payload["translation"] == r2.payload["translation"]
        assert len(state.calls) == 1
    finally:
        await client.aclose()


async def test_retry_on_429_and_500_succeeds(tmp_path: Path) -> None:
    st = FakeBodhanState(fail_status_sequence=[429, 503])
    transport, state = make_fake_bodhan_transport(st)
    client = BodhanClient(
        cache=DiskCache(tmp_path / "cache"),
        transport=transport,
        base_backoff_s=0.005,
    )
    try:
        r = await client.translate("Retry me", target_language="hi")
        assert r.attempts == 3
        assert len(state.calls) == 3
    finally:
        await client.aclose()


async def test_4xx_validation_error_is_not_retried(tmp_path: Path) -> None:
    st = FakeBodhanState(fail_status_sequence=[422])
    transport, state = make_fake_bodhan_transport(st)
    client = BodhanClient(
        cache=DiskCache(tmp_path / "cache"),
        transport=transport,
        base_backoff_s=0.005,
    )
    try:
        with pytest.raises(BodhanAPIError) as exc_info:
            await client.translate("Bad", target_language="hi")
        assert exc_info.value.retryable is False
        assert len(state.calls) == 1
    finally:
        await client.aclose()


async def test_401_retried_once_only_if_key_succeeded_earlier(tmp_path: Path) -> None:
    # Fresh key getting 401 -> no retry.
    st1 = FakeBodhanState(fail_status_sequence=[401])
    t1, _ = make_fake_bodhan_transport(st1)
    c1 = BodhanClient(cache=DiskCache(tmp_path / "c1"), transport=t1, base_backoff_s=0.005)
    try:
        with pytest.raises(BodhanAPIError) as ei:
            await c1.translate("Unauth", target_language="hi")
        assert ei.value.code == "auth_error"
        assert len(st1.calls) == 1
    finally:
        await c1.aclose()

    # Key succeeds once, then transient 401 -> retried once and succeeds!
    st2 = FakeBodhanState()
    t2, _ = make_fake_bodhan_transport(st2)
    c2 = BodhanClient(cache=DiskCache(tmp_path / "c2"), transport=t2, base_backoff_s=0.005)
    try:
        await c2.translate("First ok", target_language="hi")
        st2.fail_status_sequence = [401]
        r2 = await c2.translate("Second after transient 401", target_language="hi")
        assert r2.attempts == 2
        assert len(st2.calls) == 3
    finally:
        await c2.aclose()


async def test_speak_caches_binary_wav(tmp_path: Path) -> None:
    transport, state = make_fake_bodhan_transport()
    client = BodhanClient(cache=DiskCache(tmp_path / "cache"), transport=transport)
    try:
        r1 = await client.speak("नमस्ते दुनिया", voice="Kavya", language="hi")
        r2 = await client.speak("नमस्ते दुनिया", voice="Kavya", language="hi")
        assert r1.binary and r1.binary[:4] == b"RIFF"
        assert r2.cache_hit is True and r2.binary == r1.binary
        assert len(state.calls) == 1
    finally:
        await client.aclose()


def test_per_model_key_fallback_to_bodhan_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY", raising=False)
    monkeypatch.setenv("BODHAN_API_KEY", "fallback-key-value")
    client = BodhanClient(cache=DiskCache(tmp_path / "cache"))
    assert client._get_key("indic-translate") == "fallback-key-value"
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "specific-translate-key")
    assert client._get_key("indic-translate") == "specific-translate-key"
    # Ensure wave bytes helper works cleanly
    assert len(make_sine_wav_bytes(0.5)) > 100
