import concurrent.futures
import json

import httpx
import pytest
from bodhan_eval.providers import BudgetExceeded, Ledger, ModelClient, Unavailable


def test_cached_calls_make_no_duplicate_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-only")
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"translation": "test fixture", "truncated": False,
                                        "usage": {"completion_tokens": 5}})
    client = ModelClient(tmp_path, Ledger(1), transport=httpx.MockTransport(handle))
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: client.translate("source", "hi"), range(4)))
    assert len(calls) == 1
    assert results == ["test fixture"] * 4
    assert client.ledger.used == pytest.approx(0.0001)


def test_budget_stops_before_sending_request(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-only")
    client = ModelClient(tmp_path, Ledger(0), transport=httpx.MockTransport(lambda _: pytest.fail("sent")))
    with pytest.raises(BudgetExceeded):
        client.translate("source", "hi")


def test_offline_cache_replays_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-only")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"translation": "cached", "truncated": False}))
    first = ModelClient(tmp_path, Ledger(1), transport=transport)
    first.translate("source", "hi")
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY")
    replay = ModelClient(tmp_path, Ledger(0), offline=True)
    assert replay.translate("source", "hi") == "cached"
    assert replay.ledger.used == 0


def test_timeout_is_charged_and_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-only")
    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture")
    client = ModelClient(tmp_path, Ledger(1), transport=httpx.MockTransport(timeout))
    with pytest.raises(Unavailable):
        client.translate("source", "hi")
    assert len(calls) == 1
    assert client.ledger.used > 0
    assert client.ledger.reserved == pytest.approx(0)


def test_failed_requests_are_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-only")
    client = ModelClient(tmp_path, Ledger(1), transport=httpx.MockTransport(lambda _: httpx.Response(401)))
    with pytest.raises(Unavailable):
        client.translate("source", "hi")
    assert not list(tmp_path.glob("*.json"))


def test_translation_chunks_and_rejects_truncation(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-only")
    sizes = []
    def handle(request):
        payload = json.loads(request.content)
        sizes.append(len(payload["messages"][0]["content"]))
        return httpx.Response(200, json={"translation": "chunk", "truncated": False})
    client = ModelClient(tmp_path, Ledger(1), transport=httpx.MockTransport(handle))
    assert len(client.translate("word " * 1000, "hi").splitlines()) == 3
    assert max(sizes) <= 1800


def test_rate_limit_has_bounded_retries_and_preserves_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-only")
    monkeypatch.setattr("bodhan_eval.providers.time.sleep", lambda _: None)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"code": "rate_limit"}})
    client = ModelClient(tmp_path, Ledger(1), transport=httpx.MockTransport(handle))
    with pytest.raises(Unavailable):
        client.translate("source", "hi")
    assert len(calls) == 3
    assert client.ledger.reserved == pytest.approx(0)
    assert client.ledger.used > 0
