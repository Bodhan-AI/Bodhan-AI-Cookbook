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


async def test_real_api_response_shapes_regression(tmp_path: Path) -> None:
    """Regression tests matching exact live Bodhan response shapes (redacted) observed in smoke testing."""
    import json
    import httpx

    seen_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content.decode("utf-8"))
            seen_bodies.append(body)
            if body.get("model") == "indic-translate":
                is_trunc = body.get("max_output_tokens") == 3
                return httpx.Response(
                    200,
                    headers={"llm_provider-x-request-id": "req_live_tr"},
                    json={
                        "id": "20261121_redacted",
                        "created": 1795247458,
                        "model": "indic-translate",
                        "object": "chat.completion",
                        "system_fingerprint": "vllm-redacted",
                        "choices": [
                            {
                                "finish_reason": "length" if is_trunc else "stop",
                                "index": 0,
                                "message": {
                                    "content": "<redacted_output>",
                                    "role": "assistant",
                                    "provider_specific_fields": {"reasoning": None, "refusal": None},
                                },
                                "provider_specific_fields": {"stop_reason": None if is_trunc else 106},
                            }
                        ],
                        "usage": {"completion_tokens": 11, "prompt_tokens": 25, "total_tokens": 36},
                    },
                )
            if body.get("model") == "indic-ocr":
                if not body.get("messages"):
                    # Live API returned HTTP 200 with JSON null on malformed empty messages.
                    return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})
                return httpx.Response(
                    200,
                    json={
                        "id": "indic-ocr-redacted",
                        "created": 1795247361,
                        "model": "indic-ocr",
                        "object": "chat.completion",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "index": 0,
                                "message": {
                                    "content": "Line 1\n\nLine 2",
                                    "role": "assistant",
                                    "provider_specific_fields": {"refusal": None},
                                },
                                "provider_specific_fields": {},
                            }
                        ],
                        "usage": {"completion_tokens": 20, "prompt_tokens": 250, "total_tokens": 270},
                        "blocks": [
                            {
                                "order": 0,
                                "label": "Paragraph",
                                "type": "Text",
                                "bbox_xyxy": [23.8, 22.0, 200.0, 44.0],
                                "conf": 0.892,
                                "text": "Line 1",
                            }
                        ],
                    },
                )
        if request.url.path == "/v1/audio/speech":
            # Live API sends Content-Type: audio/mpeg while body is RIFF/WAVE PCM16.
            return httpx.Response(
                200,
                content=make_sine_wav_bytes(0.4, sample_rate=24000),
                headers={"content-type": "audio/mpeg"},
            )
        if request.url.path == "/v1/audio/transcriptions":
            # Live API sends usage: null.
            return httpx.Response(200, json={"text": "test", "usage": None})
        return httpx.Response(404, json={"detail": "Not Found"})

    client = BodhanClient(
        cache=DiskCache(tmp_path / "cache"),
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    try:
        # 1. indic-translate normal + finish_reason="length" truncation + omits script for en.
        r_en = await client.translate("test", target_language="en", script="native")
        assert r_en.payload["translation"] == "<redacted_output>"
        assert r_en.payload["truncated"] is False
        assert r_en.cost_inr == pytest.approx(cost_translate(11))
        assert "script" not in seen_bodies[-1]

        r_trunc = await client.translate("long text", target_language="hi", max_output_tokens=3)
        assert r_trunc.payload["truncated"] is True

        # 2. indic-speak returns audio/mpeg header with RIFF/WAVE bytes -> detected as wav.
        r_sp = await client.speak("test", voice="Kavya", language="hi")
        assert r_sp.payload["audio_format"] == "wav"
        assert r_sp.payload["content_type"] == "audio/mpeg"

        # 3. indic-transcribe handles usage: null cleanly.
        r_asr = await client.transcribe(r_sp.binary or b"", duration_s=1.0, language="hi")
        assert r_asr.payload["text"] == "test"
        assert r_asr.payload["usage"] is None

        # 4. indic-ocr parses choices + top-level blocks.
        r_ocr = await client.ocr(b"\x89PNG\r\n\x1a\nfake")
        assert r_ocr.payload["choices"][0]["message"]["content"] == "Line 1\n\nLine 2"
        assert len(r_ocr.payload["blocks"]) == 1
        assert r_ocr.payload["blocks"][0]["conf"] == pytest.approx(0.892)
    finally:
        await client.aclose()


async def test_real_api_error_envelopes_and_null_ocr_guard(tmp_path: Path) -> None:
    """Regression tests for live gateway error envelopes (LiteLLM + FastAPI 404/422 + OCR 200 null)."""
    import httpx

    cases = [
        # LiteLLM 400 with numeric code + semantic type
        (
            400,
            {"llm_provider-x-request-id": "req_hdr_1"},
            {"error": {"message": "unsupported language 'fr'", "type": "invalid_request_error", "param": None, "code": "400"}},
            "invalid_request_error",
            "req_hdr_1",
        ),
        # FastAPI 404 string detail
        (404, {}, {"detail": "Not Found"}, "http_404", None),
        # FastAPI 422 validation list
        (
            422,
            {},
            {"detail": [{"type": "missing", "loc": ["body", "file"], "msg": "Field required", "input": None}]},
            "validation_error",
            None,
        ),
    ]
    for status, hdrs, body, expected_code, expected_req_id in cases:
        resp = httpx.Response(status, headers=hdrs, json=body)
        code, msg, req_id = BodhanClient._parse_error(resp)
        assert code == expected_code
        assert msg
        assert req_id == expected_req_id

    # Guard against live OCR returning HTTP 200 with JSON null.
    def null_ocr(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})

    client = BodhanClient(cache=DiskCache(tmp_path / "cache_null"), transport=httpx.MockTransport(null_ocr), max_retries=0)
    try:
        with pytest.raises(BodhanAPIError) as exc_info:
            await client.ocr(b"fake_png")
        assert exc_info.value.code == "invalid_response"
    finally:
        await client.aclose()


async def test_llm_client_retries_429_and_503_and_enforces_timeout() -> None:
    """Regression test: OpenAICompatibleLLM retries 429/503 with backoff and uses >=120s timeout."""
    import httpx
    from openai import InternalServerError, RateLimitError
    from bodhan_agent.llm import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM(
        base_url="https://example.invalid/v1",
        model="test-model",
        api_key="test-key",
        timeout_s=60.0,
        max_retries=3,
        base_backoff_s=0.005,
    )
    assert llm.timeout_s >= 120.0

    attempts = {"n": 0}

    class _DummyMsg:
        content = '{"ok": true}'

    class _DummyChoice:
        message = _DummyMsg()

    class _DummyResp:
        choices = [_DummyChoice()]

    class _DummyCompletions:
        async def create(self, **_kwargs):
            attempts["n"] += 1
            req = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            if attempts["n"] == 1:
                raise RateLimitError("rate limited", response=httpx.Response(429, request=req), body={})
            if attempts["n"] == 2:
                raise InternalServerError("overloaded", response=httpx.Response(503, request=req), body={})
            return _DummyResp()

    class _DummyChat:
        completions = _DummyCompletions()

    class _DummyClient:
        chat = _DummyChat()

    llm._client = _DummyClient()  # type: ignore[assignment]
    out = await llm.complete_json(role_tag="p_llm_test", system_prompt="sys", user_prompt="usr")
    assert out == {"ok": True}
    assert attempts["n"] == 3
