import asyncio
import json

import httpx
import pytest

from app.bodhan.client import BodhanClient, BodhanError
from app.config import RateLimits, Settings, VadSettings


def settings(**kw):
    base = dict(
        base_url="https://api.bodhan.ai", transcribe_key="k1", translate_key="k2", speak_key="k3",
        limits=RateLimits(rpm=50, concurrency=2, max_retries=6), shared_limiter=False,
        my_language="en", their_language="hi", subtitle_script="native", tts_voice="Kavya",
        tts_style=None, ptt_key="ctrl_r", mic_device=None, cable_device="CABLE Input",
        monitor_tts=False, subtitle_deadline_s=15, interpret_deadline_s=20, caption_hold_s=8,
        subtitle_merge_pause_s=1.5,
        vad=VadSettings(threshold=0.5, end_silence_ms=600, soft_max_s=8, hard_max_s=25),
    )
    base.update(kw)
    return Settings(**base)


def client_with(handler, **kw):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return BodhanClient(settings(**kw), http=http)


def test_translate_contract_and_retry_on_429():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"},
                                  json={"error": {"code": "rate_limit_exceeded_error", "message": "slow down"}})
        return chat_response(" Hello ")

    async def run():
        c = client_with(handler)
        try:
            return await c.translate("नमस्ते", "en", script="roman")
        finally:
            await c.aclose()

    assert asyncio.run(run()) == "Hello"
    assert len(calls) == 2
    req = calls[-1]
    assert req.url.path == "/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer k2"
    body = json.loads(req.content)
    assert body == {"model": "indic-translate", "target_language": "en",  # no script for en
                    "messages": [{"role": "user", "content": "नमस्ते"}]}


def chat_response(content, finish_reason="stop", headers=None):
    return httpx.Response(200, headers=headers, json={
        "object": "chat.completion", "model": "indic-translate",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content}}],
    })


def test_translate_sends_script_and_flags_truncation(caplog):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return chat_response("Baithak", finish_reason="length",
                             headers={"x-ratelimit-api_key-limit-requests": "120",
                                      "x-ratelimit-api_key-limit-max_parallel_requests": "10"})

    async def run():
        c = client_with(handler)
        try:
            return await c.translate("The meeting", "hi", script="roman")
        finally:
            await c.aclose()

    with caplog.at_level("INFO"):
        assert asyncio.run(run()) == "Baithak"
    assert seen[0]["script"] == "roman" and seen[0]["target_language"] == "hi"
    assert "truncated" in caplog.text
    assert "requests=120 parallel=10" in caplog.text  # live gateway's header names are read


def test_non_retryable_error_raises_immediately():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"code": "unsupported_language", "message": "bho"}})

    async def run():
        c = client_with(handler)
        try:
            await c.translate("x", "bho")
        finally:
            await c.aclose()

    with pytest.raises(BodhanError) as exc:
        asyncio.run(run())
    assert exc.value.code == "unsupported_language" and len(calls) == 1


def test_max_retries_respected():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, headers={"retry-after": "0"}, json={"error": {"code": "internal_error"}})

    async def run():
        c = client_with(handler, limits=RateLimits(rpm=50, concurrency=2, max_retries=3))
        try:
            await c.transcribe(b"RIFF....", "hi")
        finally:
            await c.aclose()

    with pytest.raises(BodhanError):
        asyncio.run(run())
    assert len(calls) == 4  # first try + 3 retries


def test_speak_and_transcribe_contracts():
    seen = {}

    def handler(request):
        seen[request.url.path] = request
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(200, content=b"WAVDATA", headers={"content-type": "audio/wav"})
        return httpx.Response(200, json={"text": " नमस्ते "})

    async def run():
        c = client_with(handler)
        try:
            return await c.speak("नमस्ते", "hi", "Kavya", "news"), await c.transcribe(b"RIFF", "hi")
        finally:
            await c.aclose()

    audio, text = asyncio.run(run())
    assert audio == b"WAVDATA" and text == "नमस्ते"
    speech = json.loads(seen["/v1/audio/speech"].content)
    assert speech["model"] == "indic-speak" and speech["voice"] == "Kavya"
    assert json.loads(speech["instructions"]) == {"lang": "hi", "style": "news"}
    asr = seen["/v1/audio/transcriptions"]
    assert b'name="model"' in asr.content and b"indic-transcribe" in asr.content
    assert asr.headers["authorization"] == "Bearer k1"


def test_per_model_rpm_overrides():
    limits = RateLimits(rpm=50, concurrency=2, max_retries=6, speak_rpm=8)
    assert limits.rpm_for("speak") == 8
    assert limits.rpm_for("transcribe") == 50 and limits.rpm_for("translate") == 50
    c = BodhanClient(settings(limits=limits), http=httpx.AsyncClient())
    try:
        assert c._ep["speak"].limiter._rpm == 8
        assert c._ep["translate"].limiter._rpm == 50
    finally:
        asyncio.run(c.aclose())
