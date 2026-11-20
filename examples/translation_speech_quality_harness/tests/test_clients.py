"""Client behaviour against a fake HTTP transport: caching, retries, budget, offline mode."""

import asyncio
import io
import json

import httpx
import numpy as np
import pytest
import soundfile as sf

from bodhan_eval.cache import Cache
from bodhan_eval.clients import APIError, BodhanClient, CacheMiss, LLMClient
from bodhan_eval.clients import http as http_mod
from bodhan_eval.clients.llm import parse_json_object
from bodhan_eval.cost import BudgetExceeded, CostMeter


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "test-key")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(http_mod, "sleep", no_sleep)


def _wav(seconds=1.0, sr=24000):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(seconds * sr), dtype=np.float32), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _client(tmp_path, handler, **kw):
    cache = Cache(tmp_path / "c.sqlite")
    cost = CostMeter(max_inr=kw.pop("max_inr", None))
    return BodhanClient(cache, cost, transport=httpx.MockTransport(handler), **kw), cache, cost


def test_translate_caches_and_retries(tmp_path):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": "rate_limited", "message": "slow"}})
        assert req.headers["authorization"] == "Bearer test-key"
        assert req.url.path == "/v1/chat/completions"
        body = json.loads(req.content)
        assert body["model"] == "indic-translate" and body["target_language"] == "hi"
        assert [m["role"] for m in body["messages"]] == ["user"]  # gateway rejects system turns
        return httpx.Response(
            200,
            json={
                "id": "req_1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"<{body['messages'][0]['content']}>",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"completion_tokens": 10, "prompt_tokens": 5, "total_tokens": 15},
            },
        )

    async def go():
        c, cache, cost = _client(tmp_path, handler)
        r1 = await c.translate("Hello", "hi")
        r2 = await c.translate("Hello", "hi")
        await c.aclose()
        return r1, r2, cost

    r1, r2, cost = asyncio.run(go())
    assert r1.text == "<Hello>" and not r1.cached
    assert r2.cached and r2.text == r1.text
    assert len(calls) == 2  # one 429 + one success; second translate served from cache
    s = cost.summary()["services"]["translate"]
    assert s["calls"] == 1 and s["cached"] == 1 and s["inr"] > 0


def test_non_retryable_error_raises(tmp_path):
    def handler(req):
        return httpx.Response(422, json={"error": {"code": "invalid_lang", "message": "bad"}})

    async def go():
        c, _, cost = _client(tmp_path, handler)
        with pytest.raises(APIError) as ei:
            await c.translate("x", "zz")
        await c.aclose()
        return ei.value, cost

    err, cost = asyncio.run(go())
    assert err.status == 422 and err.code == "invalid_lang"
    assert cost.reserved == 0  # reservation released


def test_budget_guard_blocks_paid_calls(tmp_path):
    def handler(req):
        return httpx.Response(200, content=_wav(), headers={"content-type": "audio/wav"})

    async def go():
        c, _, _ = _client(tmp_path, handler, max_inr=0.001)
        with pytest.raises(BudgetExceeded):
            await c.speak("x" * 1000, "hi", "Kavya")  # ₹0.60 > budget
        await c.aclose()

    asyncio.run(go())


def test_offline_mode_raises_on_miss(tmp_path):
    async def go():
        c, _, _ = _client(tmp_path, lambda r: httpx.Response(500), offline=True)
        with pytest.raises(CacheMiss):
            await c.translate("x", "hi")
        await c.aclose()

    asyncio.run(go())


def test_transcribe_cached_by_audio_hash_and_cost_by_duration(tmp_path):
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(200, json={"text": "नमस्ते"})

    async def go():
        c, _, cost = _client(tmp_path, handler)
        wav = _wav(6.0)
        a = await c.transcribe(wav, "hi")
        b = await c.transcribe(wav, "hi")
        await c.aclose()
        return a, b, cost

    a, b, cost = asyncio.run(go())
    assert a.text == "नमस्ते" and b.cached and len(n) == 1
    assert abs(cost.summary()["services"]["transcribe"]["inr"] - 0.01) < 1e-6  # 6 s = 0.1 min


class _FakeCompletions:
    def __init__(self):
        self.calls = 0

    async def create(self, **kw):
        self.calls += 1

        class U:
            prompt_tokens, completion_tokens = 100, 20

        class M:
            content = '```json\n{"errors": []}\n```'

        class C:
            message = M()

        class R:
            choices = [C()]
            usage = U()

        return R()


def test_llm_json_and_cache(tmp_path):
    fake = _FakeCompletions()

    class Fake:
        chat = type("chat", (), {"completions": fake})()

    async def go():
        cache = Cache(tmp_path / "c.sqlite")
        cost = CostMeter()
        llm = LLMClient(cache, cost, model="m", client=Fake())
        msgs = [{"role": "user", "content": "judge"}]
        a = await llm.chat_json(msgs)
        b = await llm.chat_json(msgs)
        c = await llm.chat_json(msgs, sample=1)
        return a, b, c, cost

    a, b, c, cost = asyncio.run(go())
    assert a == ({"errors": []}, False) and b == ({"errors": []}, True)
    assert c[1] is False and fake.calls == 2
    assert cost.llm_tokens_in == 200


def test_parse_json_object_tolerant():
    assert parse_json_object('Sure! {"a": 1} done') == {"a": 1}
    assert parse_json_object("[1, 2]") == [1, 2]
    with pytest.raises(ValueError):
        parse_json_object("no json here")


def test_translate_finish_reason_length_means_truncated(tmp_path):
    def handler(req):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "आधा"}, "finish_reason": "length"}],
                "usage": {"completion_tokens": 4},
            },
        )

    async def go():
        c, _, _ = _client(tmp_path, handler)
        r = await c.translate("A long text", "hi")
        await c.aclose()
        return r

    r = asyncio.run(go())
    assert r.truncated and r.text == "आधा"


def test_rate_gate_shared_cooldown_after_429(tmp_path, monkeypatch):
    """After one 429, all callers wait out Retry-After together (no thundering herd)."""
    clock = {"t": 0.0}
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(http_mod, "sleep", fake_sleep)
    monkeypatch.setattr(http_mod, "monotonic", lambda: clock["t"])
    calls = []

    def handler(req):
        calls.append(clock["t"])
        if len(calls) == 1:
            return httpx.Response(
                429, headers={"retry-after": "30"}, json={"error": {"code": "rate_limited"}}
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    async def go():
        c, _, _ = _client(tmp_path, handler)
        await c.translate("one", "hi")
        await c.translate("two", "hi")  # arrives during the cooldown: must wait too
        await c.aclose()

    asyncio.run(go())
    assert calls[0] == 0.0 and calls[1] >= 30.0 and calls[2] >= 30.0


def test_rate_gate_rpm_spacing(monkeypatch):
    clock = {"t": 0.0}

    async def fake_sleep(s):
        clock["t"] += s

    monkeypatch.setattr(http_mod, "sleep", fake_sleep)
    monkeypatch.setattr(http_mod, "monotonic", lambda: clock["t"])
    gate = http_mod.RateGate(rpm=60)
    starts = []

    async def go():
        for _ in range(3):
            await gate.wait()
            starts.append(clock["t"])

    asyncio.run(go())
    assert starts == [0.0, 1.0, 2.0]


def test_rate_gate_adapts_to_429s():
    gate = http_mod.RateGate()
    assert gate.interval == 0.0
    gate.penalize()
    gate.penalize()
    assert gate.interval == 1.0  # 0.5 then doubled
    for _ in range(200):
        gate.reward()
    assert gate.interval == 0.0 or gate.interval < 0.01
    for _ in range(20):
        gate.penalize()
    assert gate.interval == gate.max_interval
