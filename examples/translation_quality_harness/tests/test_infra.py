"""Cache, budget, HTTP retries and both clients, against httpx.MockTransport."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bodhan_eval.budget import BudgetExceeded, CostTracker, speak_cost, transcribe_cost
from bodhan_eval.cache import Cache, atomic_write_text, make_key
from bodhan_eval.clients import http as http_mod
from bodhan_eval.clients.bodhan import BodhanClient, split_for_asr
from bodhan_eval.clients.http import ServiceUnavailable
from bodhan_eval.clients.llm import LLMClient, extract_json
from bodhan_eval.config import Settings
from conftest import Router, json_body, speechlike, wav_bytes


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(http_mod.asyncio, "sleep", instant)


def settings(**kw) -> Settings:
    base = {"translate_key": "t", "speak_key": "s", "transcribe_key": "r", "llm_base_url": "https://llm.test/v1", "llm_model": "m", "llm_api_key": "k"}
    base.update(kw)
    return Settings(**base)


def run(coro):
    return asyncio.run(coro)


# cache -----------------------------------------------------------------------------


def test_cache_key_is_order_independent_and_input_sensitive():
    assert make_key("a", "m", {"x": 1, "y": 2}) == make_key("a", "m", {"y": 2, "x": 1})
    assert make_key("a", "m", {"x": 1}) != make_key("a", "m", {"x": 2})
    assert make_key("a", "m", {"x": 1}) != make_key("a", "other", {"x": 1})


def test_cache_roundtrip_and_cost_memory(tmp_path):
    cache = Cache(tmp_path)
    cache.put("k", "ns", {"v": "पानी"}, 0.5)
    assert cache.get("k") == ({"v": "पानी"}, 0.5)
    path = cache.put_blob(b"RIFF....")
    assert cache.get_blob(path.stem) == b"RIFF...."
    cache.close()
    assert Cache(tmp_path).get("k") == ({"v": "पानी"}, 0.5)


def test_disabled_cache_reads_nothing(tmp_path):
    cache = Cache(tmp_path)
    cache.put("k", "ns", 1)
    assert Cache(tmp_path, enabled=False).get("k") is None


def test_atomic_write_leaves_no_temp_files(tmp_path):
    atomic_write_text(tmp_path / "a" / "x.json", "{}")
    assert (tmp_path / "a" / "x.json").read_text() == "{}"
    assert [p.name for p in (tmp_path / "a").iterdir()] == ["x.json"]


# budget ----------------------------------------------------------------------------


def test_budget_refuses_before_spending():
    async def go():
        b = CostTracker(max_inr=1.0)
        await b.reserve("m", 0.6)
        await b.settle("m", 0.6, 0.6)
        with pytest.raises(BudgetExceeded):
            await b.reserve("m", 0.5)
        return b

    b = run(go())
    assert b.spent == pytest.approx(0.6) and b.refused["m"] == 1 and b.exhausted


def test_prices_follow_the_brief():
    assert speak_cost(10_000) == pytest.approx(6.0)
    assert transcribe_cost(60) == pytest.approx(0.10)


# bodhan client ---------------------------------------------------------------------


def client_with(router, tmp_path, budget=None, **kw):
    http = httpx.AsyncClient(transport=httpx.MockTransport(router))
    return BodhanClient(settings(**kw), Cache(tmp_path), budget or CostTracker(), http), http


def test_translate_is_cached_and_costed(tmp_path):
    router = Router({"/translate": lambda r: httpx.Response(200, json={"translation": "नमस्ते", "usage": {"completion_tokens": 100}})})

    async def go():
        client, http = client_with(router, tmp_path)
        a = await client.translate("Hello", "hi")
        b = await client.translate("Hello", "hi")
        await http.aclose()
        return a, b, client

    a, b, client = run(go())
    assert a.value["translation"] == b.value["translation"] == "नमस्ते"
    assert not a.cached and b.cached and router.calls.count("/translate") == 1
    assert client.budget.spent == pytest.approx(0.002) and client.budget.attributed["indic-translate"] == pytest.approx(0.004)


def test_translate_falls_back_to_chat_and_remembers_route(tmp_path):
    def chat(request):
        body = json_body(request)
        assert body["model"] == "indic-translate" and body["temperature"] == 0
        assert body["messages"][0]["content"].startswith("Translate the following text into Hindi:")
        return httpx.Response(200, json={"choices": [{"message": {"content": "translated"}, "finish_reason": "stop"}],
                                         "usage": {"completion_tokens": 10}})

    router = Router({"/translate": lambda r: httpx.Response(404), "/v1/chat/completions": chat})

    async def go():
        client, http = client_with(router, tmp_path)
        first = await client.translate("First", "hi")
        second = await client.translate("Second", "hi")
        await http.aclose()
        return first, second

    assert all(r.value["translation"] == "translated" for r in run(go()))
    assert router.calls == ["/translate", "/v1/chat/completions", "/v1/chat/completions"]


def test_translate_reserves_maximum_output_cost_before_request(tmp_path):
    router = Router({})

    async def go():
        # The old average-token estimate would fit, but max_output_tokens would not.
        client, http = client_with(router, tmp_path, budget=CostTracker(max_inr=0.001))
        with pytest.raises(BudgetExceeded):
            await client.translate("Hello", "hi")
        await http.aclose()

    run(go())
    assert router.calls == []


def test_queued_calls_recheck_disabled_service_before_http(tmp_path):
    router = Router({"/translate": lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})})

    async def go():
        client, http = client_with(router, tmp_path, concurrency={"translate": 1})
        outcomes = await asyncio.gather(*(client.translate(str(i), "hi") for i in range(5)), return_exceptions=True)
        await http.aclose()
        assert all(isinstance(outcome, ServiceUnavailable) for outcome in outcomes)
        assert client.budget.reserved == 0

    run(go())
    assert router.calls == ["/translate"]


def test_queued_calls_reserve_only_when_admitted(tmp_path):
    router = Router({"/translate": lambda r: httpx.Response(200, json={"translation": "ok", "usage": {"completion_tokens": 1}})})

    async def go():
        # Enough for one maximum-size call plus the actual cost of previous calls.
        client, http = client_with(router, tmp_path, budget=CostTracker(max_inr=0.002), concurrency={"translate": 1})
        results = await asyncio.gather(*(client.translate(str(i), "hi") for i in range(5)))
        await http.aclose()
        assert len(results) == 5 and not client.budget.exhausted

    run(go())


def test_retry_on_429_then_success(tmp_path):
    state = {"n": 0}

    def handler(_):
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(429, json={"error": {"message": "slow down", "code": "rate_limit_exceeded_error"}})
        return httpx.Response(200, json={"translation": "ok"})

    async def go():
        client, http = client_with(Router({"/translate": handler}), tmp_path)
        out = await client.translate("x", "hi")
        await http.aclose()
        return out

    assert run(go()).value["translation"] == "ok" and state["n"] == 3


def test_auth_error_disables_the_model(tmp_path):
    router = Router({"/translate": lambda r: httpx.Response(401, json={"error": {"message": "bad key", "code": "invalid_api_key_error"}})})

    async def go():
        client, http = client_with(router, tmp_path)
        with pytest.raises(ServiceUnavailable):
            await client.translate("x", "hi")
        with pytest.raises(ServiceUnavailable):
            await client.translate("y", "hi")
        await http.aclose()
        return client

    client = run(go())
    assert not client.available("indic-translate") and router.calls.count("/translate") == 1


def test_quota_429_is_not_retried(tmp_path):
    router = Router({"/translate": lambda r: httpx.Response(429, json={"error": {"message": "monthly budget exhausted", "code": "rate_limit_exceeded_error"}})})

    async def go():
        client, http = client_with(router, tmp_path)
        with pytest.raises(ServiceUnavailable):
            await client.translate("x", "hi")
        await http.aclose()

    run(go())
    assert router.calls.count("/translate") == 1


def test_balance_based_concurrency_limit_is_retried_not_disabled(tmp_path):
    state = {"calls": 0}

    def handler(_):
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(429, json={"error": {"message": "Current concurrency exceeds your limit based on remaining balance. Please top up."}})
        return httpx.Response(200, json={"translation": "ok"})

    async def go():
        client, http = client_with(Router({"/translate": handler}), tmp_path)
        out = await client.translate("source", "hi")
        await http.aclose()
        assert out.value["translation"] == "ok" and client.available("indic-translate")

    run(go())
    assert state["calls"] == 2


def test_offline_bodhan_reads_cache_and_never_requests_on_miss(tmp_path):
    router = Router({})

    async def go():
        client, http = client_with(router, tmp_path, offline=True, translate_key=None)
        text = "Source"
        from bodhan_eval.budget import estimate_translate_tokens

        payload = {"text": text, "target_language": "hi", "max_output_tokens": estimate_translate_tokens(text) * 2 + 64}
        client.cache.put(make_key("translate", "indic-translate", payload), "indic-translate", {"translation": "cached", "completion_tokens": 1}, 0.001)
        assert (await client.translate(text, "hi")).value["translation"] == "cached"
        with pytest.raises(ServiceUnavailable, match="offline cache miss"):
            await client.translate("uncached", "hi")
        assert client.budget.spent == 0
        await http.aclose()

    run(go())
    assert router.calls == []


def test_offline_judge_never_requests_on_cache_miss(tmp_path):
    router = Router({})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
            llm = LLMClient(settings(offline=True), Cache(tmp_path), http)
            with pytest.raises(ServiceUnavailable, match="offline cache miss"):
                await llm.complete_json([{"role": "user", "content": "test"}], namespace="test")

    run(go())
    assert router.calls == []


def test_missing_key_never_calls_the_api(tmp_path):
    router = Router({})

    async def go():
        client, http = client_with(router, tmp_path, speak_key=None)
        with pytest.raises(ServiceUnavailable):
            await client.speak("नमस्ते", "hi", "Kavya")
        await http.aclose()

    run(go())
    assert router.calls == []


def test_budget_blocks_speech_before_the_call(tmp_path):
    router = Router({"/v1/audio/speech": lambda r: httpx.Response(200, content=wav_bytes(speechlike(1.0)), headers={"content-type": "audio/wav"})})

    async def go():
        client, http = client_with(router, tmp_path, budget=CostTracker(max_inr=0.001))
        with pytest.raises(BudgetExceeded):
            await client.speak("क" * 100, "hi", "Kavya")
        await http.aclose()

    run(go())
    assert router.calls == []


def test_speak_payload_and_transcribe_chunking(tmp_path):
    seen = {}

    def speak(request):
        seen["speak"] = json_body(request)
        return httpx.Response(200, content=wav_bytes(speechlike(1.0)), headers={"content-type": "audio/wav"})

    def transcribe(request):
        seen.setdefault("asr", 0)
        seen["asr"] += 1
        return httpx.Response(200, json={"text": "भाग"})

    router = Router({"/v1/audio/speech": speak, "/v1/audio/transcriptions": transcribe})

    async def go():
        client, http = client_with(router, tmp_path)
        audio = await client.speak("नमस्ते", "hi", "Kavya", style="news")
        result = await client.transcribe(speechlike(60.0, rate=16000), "hi")
        again = await client.transcribe(speechlike(60.0, rate=16000), "hi")
        await http.aclose()
        return audio, result, again

    audio, result, again = run(go())
    assert audio.value[:4] == b"RIFF"
    assert seen["speak"]["model"] == "indic-speak" and json.loads(seen["speak"]["instructions"]) == {"lang": "hi", "style": "news"}
    assert len(result.value["chunks"]) == 3 and seen["asr"] == 3 and again.cached


def test_asr_windows_stay_under_limit():
    clip = speechlike(70.0, rate=16000)
    bounds = split_for_asr(clip)
    assert bounds[0][0] == 0 and bounds[-1][1] == len(clip.samples)
    assert all((b - a) / 16000 <= 25.0 for a, b in bounds)


# llm client ------------------------------------------------------------------------


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"a": {"b": "}"}} done') == {"a": {"b": "}"}}


def test_llm_falls_back_when_response_format_is_rejected(tmp_path):
    bodies = []

    def chat(request):
        body = json_body(request)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format is not supported", "code": "invalid_request_error"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": 'Here: {"errors": []}'}, "finish_reason": "stop"}], "usage": {}})

    async def go():
        http = httpx.AsyncClient(transport=httpx.MockTransport(Router({"/chat/completions": chat})))
        llm = LLMClient(settings(), Cache(tmp_path), http)
        first = await llm.complete_json([{"role": "user", "content": "json please"}], namespace="t")
        second = await llm.complete_json([{"role": "user", "content": "json please, again"}], namespace="t")
        await http.aclose()
        return first, second

    first, second = run(go())
    assert first == ({"errors": []}, False) and second[0] == {"errors": []}
    assert "response_format" in bodies[0] and all("response_format" not in b for b in bodies[1:])
    assert len(bodies) == 3  # the downgrade is remembered


def test_llm_retries_empty_reply_with_more_tokens(tmp_path):
    budgets = []

    def chat(request):
        body = json_body(request)
        budgets.append(body["max_tokens"])
        if len(budgets) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]})

    async def go():
        http = httpx.AsyncClient(transport=httpx.MockTransport(Router({"/chat/completions": chat})))
        llm = LLMClient(settings(), Cache(tmp_path), http)
        out = await llm.complete_json([{"role": "user", "content": "json"}], namespace="t")
        await http.aclose()
        return out

    assert run(go())[0] == {"ok": True} and budgets[1] == 2 * budgets[0]
