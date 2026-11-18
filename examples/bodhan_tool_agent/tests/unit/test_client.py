"""BodhanClient against mocked endpoints: caching, retries, error mapping, budgets, concurrency."""

import asyncio
import json

import httpx
import pytest
import respx

from bodhan_core.cache import Cache
from bodhan_core.client import BodhanClient
from bodhan_core.config import Settings
from bodhan_core.errors import ErrorCode, ToolError
from bodhan_core.ledger import Ledger

BASE = "https://api.bodhan.ai"
KEYS = {m: f"test-{m}" for m in ("indic-transcribe", "indic-translate", "indic-speak", "indic-ocr")}


def make_client(tmp_path, *, keys=KEYS, ledger=None, retries=3, concurrency=4):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    settings = Settings(bodhan_keys=keys, max_retries=retries, max_concurrency=concurrency, call_timeout_s=5)
    client = BodhanClient(settings, ledger=ledger or Ledger(), cache=Cache(tmp_path / "c.sqlite"), sleep=fake_sleep)
    return client, sleeps


@respx.mock
async def test_translate_success_and_cache_hit(tmp_path):
    route = respx.post(f"{BASE}/translate").mock(
        return_value=httpx.Response(200, json={"translation": "नमस्ते", "usage": {"output_tokens": 5000}})
    )
    client, _ = make_client(tmp_path)
    r1 = await client.translate("Hello", target_language="hi")
    r2 = await client.translate("Hello", target_language="hi")
    assert r1.data["translation"] == "नमस्ते" and not r1.cached
    assert r1.cost_inr == pytest.approx(0.10)  # 5000 output tokens at ₹0.20 / 10k
    assert r2.cached and r2.cost_inr == 0
    assert route.call_count == 1  # same input never sent twice
    assert client.ledger.spent_inr == pytest.approx(0.10)
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"text": "Hello", "target_language": "hi"}
    assert route.calls[0].request.headers["authorization"] == "Bearer test-indic-translate"


@respx.mock
async def test_cache_persists_across_clients(tmp_path):
    route = respx.post(f"{BASE}/translate").mock(return_value=httpx.Response(200, json={"translation": "x"}))
    c1, _ = make_client(tmp_path)
    await c1.translate("Hi", target_language="ta")
    c2, _ = make_client(tmp_path)
    assert (await c2.translate("Hi", target_language="ta")).cached
    assert route.call_count == 1


@respx.mock
async def test_retry_on_429_honours_retry_after(tmp_path):
    route = respx.post(f"{BASE}/translate").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "2"}, json={"error": {"message": "slow down", "code": "rate_limit"}}),
            httpx.Response(503, json={"error": {"message": "busy"}}),
            httpx.Response(200, json={"translation": "ok"}),
        ]
    )
    client, sleeps = make_client(tmp_path)
    r = await client.translate("Hi", target_language="te")
    assert r.data["translation"] == "ok" and r.attempts == 3
    assert route.call_count == 3
    assert sleeps[0] == 2.0 and 1.0 <= sleeps[1] <= 2.5


@respx.mock
async def test_retries_exhausted_gives_structured_error(tmp_path):
    respx.post(f"{BASE}/translate").mock(return_value=httpx.Response(500, json={"error": {"message": "boom"}}))
    client, sleeps = make_client(tmp_path, retries=2)
    with pytest.raises(ToolError) as exc:
        await client.translate("Hi", target_language="te")
    assert exc.value.info.code is ErrorCode.UPSTREAM_ERROR and exc.value.info.retryable
    assert len(sleeps) == 2


@respx.mock
async def test_quota_429_is_not_retried(tmp_path):
    route = respx.post(f"{BASE}/translate").mock(
        return_value=httpx.Response(429, json={"error": {"message": "Insufficient credit", "code": "budget_exceeded"}})
    )
    client, sleeps = make_client(tmp_path)
    with pytest.raises(ToolError) as exc:
        await client.translate("Hi", target_language="te")
    assert exc.value.info.code is ErrorCode.QUOTA_EXHAUSTED
    assert route.call_count == 1 and sleeps == []


@respx.mock
async def test_400_is_not_retried_and_keeps_request_id(tmp_path):
    respx.post(f"{BASE}/translate").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad target", "code": "invalid", "request_id": "r-1"}})
    )
    client, sleeps = make_client(tmp_path)
    with pytest.raises(ToolError) as exc:
        await client.translate("Hi", target_language="xx")
    assert exc.value.info.code is ErrorCode.INVALID_ARGUMENT
    assert "r-1" in exc.value.info.message and sleeps == []


@respx.mock
async def test_timeout_retries_then_fails(tmp_path):
    respx.post(f"{BASE}/translate").mock(side_effect=httpx.ReadTimeout("slow"))
    client, sleeps = make_client(tmp_path, retries=1)
    with pytest.raises(ToolError) as exc:
        await client.translate("Hi", target_language="te")
    assert exc.value.info.code is ErrorCode.UPSTREAM_TIMEOUT and len(sleeps) == 1


async def test_missing_key(tmp_path):
    client, _ = make_client(tmp_path, keys={})
    with pytest.raises(ToolError) as exc:
        await client.translate("Hi", target_language="te")
    assert exc.value.info.code is ErrorCode.MISSING_API_KEY
    assert "BODHAN_TRANSLATE_KEY" in exc.value.info.hint


@respx.mock
async def test_budget_refuses_before_calling(tmp_path):
    route = respx.post(f"{BASE}/v1/audio/speech").mock(return_value=httpx.Response(200, content=b"RIFF"))
    client, _ = make_client(tmp_path, ledger=Ledger(limit_inr=0.01))
    with pytest.raises(ToolError) as exc:
        await client.speak("x" * 100, voice="Anitha", lang="ta")  # 100 chars = ₹0.06
    assert exc.value.info.code is ErrorCode.BUDGET_EXCEEDED
    assert route.call_count == 0


@respx.mock
async def test_speak_returns_audio_and_attempt_bypasses_cache(tmp_path):
    route = respx.post(f"{BASE}/v1/audio/speech").mock(
        return_value=httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})
    )
    client, _ = make_client(tmp_path)
    r = await client.speak("வணக்கம்", voice="Anitha", lang="ta", style="news")
    assert r.content == b"RIFFwav" and r.data is None
    body = json.loads(route.calls[0].request.content)
    assert json.loads(body["instructions"]) == {"lang": "ta", "style": "news"}
    assert "attempt" not in body
    await client.speak("வணக்கம்", voice="Anitha", lang="ta", style="news")  # cached
    await client.speak("வணக்கம்", voice="Anitha", lang="ta", style="news", attempt=1)  # deliberate retry
    assert route.call_count == 2


@respx.mock
async def test_transcribe_multipart_and_cost(tmp_path):
    route = respx.post(f"{BASE}/v1/audio/transcriptions").mock(return_value=httpx.Response(200, json={"text": "नमस्ते"}))
    client, _ = make_client(tmp_path)
    r = await client.transcribe(b"RIFF....", filename="a.wav", duration_s=30, language="hi")
    assert r.data["text"] == "नमस्ते" and r.cost_inr == pytest.approx(0.05)
    req = route.calls[0].request
    assert b'name="model"' in req.content and b"indic-transcribe" in req.content and b'filename="a.wav"' in req.content


@respx.mock
async def test_ocr_sends_data_url(tmp_path):
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "# Title"}}], "blocks": []})
    )
    client, _ = make_client(tmp_path)
    r = await client.ocr(b"\x89PNG", mime="image/png")
    assert r.data["choices"][0]["message"]["content"] == "# Title" and r.cost_inr == pytest.approx(0.20)
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "indic-ocr"
    assert body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")


@respx.mock
async def test_concurrency_limit(tmp_path):
    in_flight = peak = 0

    async def slow(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json={"translation": "ok"})

    respx.post(f"{BASE}/translate").mock(side_effect=slow)
    client, _ = make_client(tmp_path, concurrency=2)
    await asyncio.gather(*(client.translate(f"t{i}", target_language="hi") for i in range(8)))
    assert peak == 2
