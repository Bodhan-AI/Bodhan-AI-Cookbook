"""test_generate: --generate: fill missing translations/audio through the (mocked) Bodhan API.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from bodhan_eval import pipeline
from bodhan_eval.clients import BodhanClient, BodhanError, LLMClient, MissingKeyError, OfflineCacheMiss, extract_json, generate_missing, safe_filename
from bodhan_eval.config import Prices, Settings
from bodhan_eval.dataset import Item
from bodhan_eval.infra import BudgetExceeded, Cache, CostLedger, make_key
from bodhan_eval.pipeline import RunOptions

from .conftest import make_wav


# ==================== cache_cost
def test_key_is_stable_and_input_sensitive():
    k1 = make_key("indic-translate", "/translate", {"target_language": "hi", "script": "native"}, "hello")
    k2 = make_key("indic-translate", "/translate", {"script": "native", "target_language": "hi"}, "hello")
    assert k1 == k2  # param order does not matter
    assert k1 != make_key("indic-translate", "/translate", {"target_language": "ta", "script": "native"}, "hello")
    assert k1 != make_key("indic-translate", "/translate", {"target_language": "hi", "script": "native"}, "hello!")
    assert k1 == make_key("indic-translate", "/translate", {"target_language": "hi", "script": "native"}, b"hello")


def test_cache_roundtrip_and_persistence(tmp_path):
    path = tmp_path / "c.sqlite"
    c = Cache(path)
    assert c.get("k") is None
    c.put("k", "speak", {"duration_s": 1.5}, b"\x00\x01")
    c.add_spend("speak", 100, 0.06)
    c.close()
    c2 = Cache(path)
    e = c2.get("k")
    assert e.meta == {"duration_s": 1.5} and e.payload == b"\x00\x01"
    assert c2.lifetime_spend() == {"speak": pytest.approx(0.06)}
    c2.close()


def test_cache_read_disabled_still_writes(tmp_path):
    c = Cache(tmp_path / "c.sqlite", read=False)
    c.put("k", "x", {}, b"")
    assert c.get("k") is None
    c.close()
    assert Cache(tmp_path / "c.sqlite").get("k") is not None


def test_prices():
    led = CostLedger(Prices())
    assert led.price("translate", 10_000) == pytest.approx(0.20)
    assert led.price("speak", 10_000) == pytest.approx(6.0)
    assert led.price("transcribe", 60) == pytest.approx(0.10)
    assert led.price("transcribe", 0.2) == pytest.approx(0.10 / 60)  # billed per started second
    assert led.price("llm", 1_000_000) == 0.0


def test_budget_gate_run_cap():
    led = CostLedger(Prices(), max_run_inr=0.10)
    r = led.reserve("speak", 0.06)
    with pytest.raises(BudgetExceeded):
        led.reserve("speak", 0.06)  # in-flight reservation counts
    led.commit(r, 100)  # 100 chars = 0.06
    assert led.run_new_total == pytest.approx(0.06)
    assert led.summary()["per_service"]["speak"]["skipped_budget"] == 1


def test_budget_gate_lifetime_cap():
    led = CostLedger(Prices(), lifetime_cap_inr=8.0, lifetime_spent_at_start=7.99)
    with pytest.raises(BudgetExceeded):
        led.reserve("speak", 0.06)


def test_release_and_cached_accounting():
    sink = []
    led = CostLedger(Prices(), max_run_inr=1.0, spend_sink=lambda *a: sink.append(a))
    r = led.reserve("translate", 0.5)
    led.release(r)
    led.reserve("translate", 0.9)  # possible only because the first was released
    led.record_cached("speak", 10_000)
    s = led.summary()
    assert s["new_inr"] == 0 and s["cached_equivalent_inr"] == pytest.approx(6.0)
    assert sink == []  # nothing committed, nothing persisted


# ==================== clients
def run(coro):
    return asyncio.run(coro)


class Recorder:
    """Mock Bodhan API recording every request."""

    def __init__(self, fail_first: int = 0, status: int = 503):
        self.calls = []
        self.fail_first = fail_first
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail_first > 0:
            self.fail_first -= 1
            return httpx.Response(self.status, json={"error": {"message": "busy", "code": "internal_error"}}, headers={"retry-after": "0"})
        path = request.url.path
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            assert body["model"] == "indic-translate" and len(body["messages"]) == 1
            text = body["messages"][0]["content"]
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": f"<{body['target_language']}>{text}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
                },
            )
        if path == "/v1/audio/speech":
            return httpx.Response(200, content=make_wav(1.0), headers={"content-type": "audio/wav"})
        if path == "/v1/audio/transcriptions":
            assert b"indic-transcribe" in request.content
            return httpx.Response(200, json={"text": "transcript"})
        return httpx.Response(404)


def make_client(settings, cache, ledger, handler, **kw):
    return BodhanClient(settings, cache, ledger, transport=httpx.MockTransport(handler), **kw)


def test_translate_caches_and_costs(settings, cache, ledger):
    rec = Recorder()

    async def go():
        async with make_client(settings, cache, ledger, rec) as c:
            a = await c.translate("Water boils.", "hi")
            b = await c.translate("Water boils.", "hi")
            return a, b

    a, b = run(go())
    assert a.text == "<hi>Water boils." and not a.cached
    assert b.text == a.text and b.cached
    assert len(rec.calls) == 1
    assert rec.calls[0].headers["authorization"] == "Bearer t-key"
    s = ledger.summary()["per_service"]["translate"]
    assert s["new_calls"] == 1 and s["cached_calls"] == 1
    assert s["new_inr"] == pytest.approx(7 / 10_000 * 0.20)
    assert cache.lifetime_spend()["translate"] == pytest.approx(7 / 10_000 * 0.20)


def test_rerun_from_cache_costs_nothing(settings, cache):
    rec = Recorder()

    async def go(ledger):
        async with make_client(settings, cache, ledger, rec) as c:
            await c.translate("Hello", "ta")
            sp = await c.speak("வணக்கம்", "ta")
            await c.transcribe(sp.wav, "ta")

    first = CostLedger(Prices(), max_run_inr=1.0)
    run(go(first))
    second = CostLedger(Prices(), max_run_inr=1.0)
    run(go(second))
    assert len(rec.calls) == 3
    assert first.summary()["new_inr"] > 0
    assert second.summary()["new_inr"] == 0
    assert second.summary()["cached_equivalent_inr"] == pytest.approx(first.summary()["new_inr"])


def test_speak_request_shape(settings, cache, ledger):
    rec = Recorder()

    async def go():
        async with make_client(settings, cache, ledger, rec) as c:
            return await c.speak("नमस्ते", "hi")

    res = run(go())
    body = json.loads(rec.calls[0].content)
    assert body == {"model": "indic-speak", "input": "नमस्ते", "voice": "Kavya", "instructions": '{"lang": "hi"}'}
    assert res.duration_s == pytest.approx(1.0, abs=0.01)
    assert ledger.summary()["per_service"]["speak"]["new_inr"] == pytest.approx(6 / 10_000 * 6.0)


def test_retries_on_503(settings, cache, ledger, monkeypatch):
    monkeypatch.setattr("bodhan_eval.infra.random.random", lambda: 0.0)

    async def no_sleep(_):
        return None

    monkeypatch.setattr("bodhan_eval.infra.asyncio.sleep", no_sleep)
    rec = Recorder(fail_first=2)

    async def go():
        async with make_client(settings, cache, ledger, rec) as c:
            return await c.translate("x", "bn")

    assert run(go()).text == "<bn>x"
    assert len(rec.calls) == 3


def test_non_retryable_error_releases_budget(settings, cache, ledger):
    rec = Recorder(fail_first=1, status=400)

    async def go():
        async with make_client(settings, cache, ledger, rec) as c:
            await c.translate("x", "bn")

    with pytest.raises(BodhanError):
        run(go())
    assert len(rec.calls) == 1
    assert ledger.run_new_total == 0 and ledger._reserved == pytest.approx(0)


def test_budget_blocks_call_before_http(settings, cache):
    rec = Recorder()
    led = CostLedger(Prices(), max_run_inr=0.001)

    async def go():
        async with make_client(settings, cache, led, rec) as c:
            await c.speak("a" * 1000, "hi")  # ₹0.6 > cap

    with pytest.raises(BudgetExceeded):
        run(go())
    assert rec.calls == []


def test_offline_and_missing_key(cache, ledger, settings):
    rec = Recorder()

    async def offline():
        async with make_client(settings, cache, ledger, rec, offline=True) as c:
            await c.translate("never cached", "hi")

    with pytest.raises(OfflineCacheMiss):
        run(offline())

    async def nokey():
        async with make_client(Settings(bodhan_base_url="https://api.test"), cache, ledger, rec) as c:
            await c.translate("x", "hi")

    with pytest.raises(MissingKeyError):
        run(nokey())
    assert rec.calls == []


def test_transcribe_rejects_long_clip(settings, cache, ledger):
    async def go():
        async with make_client(settings, cache, ledger, Recorder()) as c:
            await c.transcribe(make_wav(31.0, sr=8000), "hi")

    with pytest.raises(BodhanError, match="audio_too_long|30s"):
        run(go())


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"errors": []} hope this helps') == {"errors": []}
    assert extract_json("no json here") is None
    assert extract_json("[1, 2]") is None


class FakeCompletions:
    def __init__(self, reject_json_mode=False):
        self.calls = []
        self.reject_json_mode = reject_json_mode

    async def create(self, **kwargs):
        import openai

        self.calls.append(kwargs)
        if self.reject_json_mode and "response_format" in kwargs:
            raise openai.BadRequestError("response_format unsupported", response=httpx.Response(400, request=httpx.Request("POST", "https://x")), body=None)
        msg = SimpleNamespace(content='{"verdict": "ok"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3))


def test_llm_cache_variant_and_json_fallback(settings, cache, ledger):
    comp = FakeCompletions(reject_json_mode=True)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=comp))

    async def go():
        llm = LLMClient(settings, cache, ledger, client=fake)
        a = await llm.complete_json("sys", "user")
        b = await llm.complete_json("sys", "user")
        c = await llm.complete_json("sys", "user", variant="rejudge-1")
        return a, b, c

    a, b, c = run(go())
    assert a.data == {"verdict": "ok"} and not a.cached
    assert b.cached and b.data == a.data
    assert not c.cached  # a different variant is a separate (cached) call
    # 1st call: json-mode rejected then plain; variant call: plain only
    assert ["response_format" in k for k in comp.calls] == [True, False, False]
    assert all(k["temperature"] == 0 for k in comp.calls)


class RateLimited(Recorder):
    """Mock API: the first request to ``limited_path`` gets a 429 (optionally with Retry-After)."""

    def __init__(self, limited_path: str, retry_after: str | None):
        super().__init__()
        self.limited_path, self.retry_after, self.times, self.hit = limited_path, retry_after, [], False

    def __call__(self, request):
        self.times.append((time.monotonic(), request.url.path))
        if request.url.path == self.limited_path and not self.hit:
            self.hit = True
            headers = {"retry-after": self.retry_after} if self.retry_after is not None else {}
            return httpx.Response(429, json={"detail": "Maximum rate limit reached for the model"}, headers=headers)
        return super().__call__(request)


@pytest.fixture
def no_backoff(monkeypatch):
    """Turn off the per-task retry backoff so only the shared cooldown can delay requests."""

    async def no_sleep(_):
        return None

    monkeypatch.setattr("bodhan_eval.infra.asyncio.sleep", no_sleep)


def test_429_honours_retry_after_then_resumes(settings, cache, ledger, no_backoff):
    rec = RateLimited("/v1/chat/completions", "0.4")

    async def go():
        async with make_client(settings, cache, ledger, rec) as c:
            return await asyncio.gather(*(c.translate(f"t{i}", "hi") for i in range(5)))

    t0 = time.monotonic()
    outs = run(go())
    assert [o.text for o in outs] == [f"<hi>t{i}" for i in range(5)], "every call completes after the pause"
    first = rec.times[0][0]
    later = [t for t, _ in rec.times[1:]]
    assert later and min(later) - first >= 0.39, "nothing is sent before Retry-After has passed"
    assert time.monotonic() - t0 < 0.4 + 1.5, "calls resume promptly after the pause"


def test_429_without_retry_after_uses_default_pause(settings, cache, ledger, no_backoff, monkeypatch):
    monkeypatch.setattr("bodhan_eval.clients.RATE_LIMIT_PAUSE_S", 0.25)
    rec = RateLimited("/v1/audio/speech", None)

    async def go():
        async with make_client(settings, cache, ledger, rec) as c:
            return await c.speak("abc", "hi")

    assert run(go()).wav
    (t429, _), (t_ok, _) = rec.times
    assert t_ok - t429 >= 0.24


def test_429_cooldown_is_per_service(settings, cache, ledger, no_backoff):
    rec = RateLimited("/v1/audio/speech", "0.5")

    async def go():
        async with make_client(settings, cache, ledger, rec) as c:
            speak = asyncio.create_task(c.speak("abc", "hi"))
            await asyncio.sleep(0.05)  # let the speech request hit its 429 first
            tr = await c.translate("x", "hi")
            return tr, await speak

    tr, sp = run(go())
    assert tr.text == "<hi>x" and sp.wav
    t429 = next(t for t, p in rec.times if p == "/v1/audio/speech")
    t_tr = next(t for t, p in rec.times if p == "/v1/chat/completions")
    t_sp2 = [t for t, p in rec.times if p == "/v1/audio/speech"][1]
    assert t_tr - t429 < 0.3, "translate is not delayed by the speech cooldown"
    assert t_sp2 - t429 >= 0.49, "speech waited for its own cooldown"


def test_parse_retry_after_forms():
    from bodhan_eval.infra import parse_retry_after

    assert parse_retry_after("42") == 42.0 and parse_retry_after(None) is None and parse_retry_after("soon") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:30 GMT", now=1445412500.0) == pytest.approx(10.0)
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412500.0) == 0.0


# ==================== generate
def client(settings, cache, ledger, rec):
    return BodhanClient(settings, cache, ledger, transport=httpx.MockTransport(rec))


def test_generate_translation_then_audio(settings, cache, ledger, tmp_path):
    rec = Recorder()
    short = Item("s1", "Hi.", "hi")
    long_ = Item("s2", "x" * 200, "hi")
    given = Item("s3", "Hi.", "hi", translation="done", raw={"audio": "missing.wav"})

    async def go():
        async with client(settings, cache, ledger, rec) as c:
            return [await generate_missing(c, it, tmp_path, tts_max_chars=50) for it in (short, long_, given)]

    r_short, r_long, r_given = asyncio.run(go())
    assert r_short.translation == "<hi>Hi." and r_short.audio == "audio/s1.wav"
    assert (tmp_path / "audio" / "s1.wav").exists() and short.audio == tmp_path / "audio" / "s1.wav"
    assert r_long.translation and r_long.audio is None and r_long.skipped == ["audio:too_long"]
    assert r_given.to_dict() == {}, "a supplied audio path is never re-synthesized"
    paths = [c.url.path for c in rec.calls]
    assert paths.count("/v1/chat/completions") == 2 and paths.count("/v1/audio/speech") == 1


def test_generate_budget_stop_is_per_item(settings, cache, tmp_path):
    rec = Recorder()
    led = CostLedger(Prices(), max_run_inr=0.001)  # enough for a translation, not for speech
    it = Item("b1", "Hello there.", "hi")

    async def go():
        async with client(settings, cache, led, rec) as c:
            return await generate_missing(c, it, tmp_path, tts_max_chars=10_000)

    res = asyncio.run(go())
    assert res.translation and res.skipped == ["audio:budget"] and res.budget_skipped
    assert not any(c.url.path == "/v1/audio/speech" for c in rec.calls)


def test_safe_filename():
    assert safe_filename("d001") == "d001"
    a, b = safe_filename("a/b"), safe_filename("a:b")
    assert a != b and "/" not in a and ":" not in b


def test_pipeline_generate_end_to_end(settings, tmp_path, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(pipeline, "BodhanClient", lambda *a, **k: BodhanClient(*a, transport=httpx.MockTransport(rec), **k))
    ds = tmp_path / "d.jsonl"
    rows = [{"id": "g1", "source": "The sun is a star.", "lang": "hi"}, {"id": "g2", "source": "Rivers flow.", "lang": "ta", "translation": "given"}]
    ds.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "rep"
    opts = RunOptions(dataset=ds, langs=["hi", "ta"], out=out, generate=True, cache_dir=tmp_path / "cache", max_cost_inr=1.0)
    asyncio.run(pipeline.run(opts, settings))
    items = {r["id"]: r for r in map(json.loads, (out / "items.jsonl").read_text(encoding="utf-8").splitlines())}
    assert items["g1"]["generated"] == {"translation": "<hi>The sun is a star.", "audio": "audio/g1.wav"}
    assert items["g1"]["inputs"] == {"reference": False, "translation": True, "audio": True}
    assert items["g2"]["generated"] == {"audio": "audio/g2.wav"}
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["generation"] == {"audio": 2, "translations": 1}
    # rerun from cache: identical report, no new HTTP calls
    n_calls = len(rec.calls)
    first = (out / "items.jsonl").read_bytes()
    asyncio.run(pipeline.run(opts, settings))
    assert len(rec.calls) == n_calls and (out / "items.jsonl").read_bytes() == first


def test_generate_survives_exhausted_retries(settings, cache, ledger, tmp_path, monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("bodhan_eval.infra.asyncio.sleep", no_sleep)
    rec = Recorder(fail_first=1000, status=429)
    it = Item("r1", "Hello.", "hi")

    async def go():
        async with client(settings, cache, ledger, rec) as c:
            return await generate_missing(c, it, tmp_path)

    res = asyncio.run(go())
    assert res.translation is None and res.errors == ["translation:rate_limited"]
    assert it.translation is None and ledger.summary()["new_inr"] == 0
