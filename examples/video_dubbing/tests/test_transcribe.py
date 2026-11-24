import json

import requests

from bodhan_dub import api, transcribe


class FakeResp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code, self._p, self.headers, self.text = status, payload or {}, headers or {}, "x"

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._p


def test_retry_then_success(monkeypatch):
    calls = iter([FakeResp(429), FakeResp(503), FakeResp(200, {"text": "hi"})])
    monkeypatch.setattr(api.requests, "post", lambda *a, **k: next(calls))
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    assert api.post_with_retry("u", "k").status_code == 200


def test_retry_gives_up_returns_last_response(monkeypatch):
    monkeypatch.setattr(api.requests, "post", lambda *a, **k: FakeResp(429))
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    assert api.post_with_retry("u", "k", max_attempts=3).status_code == 429


def test_no_retry_on_client_error(monkeypatch):
    n = {"c": 0}

    def post(*a, **k):
        n["c"] += 1
        return FakeResp(400)

    monkeypatch.setattr(api.requests, "post", post)
    assert api.post_with_retry("u", "k").status_code == 400 and n["c"] == 1


def test_network_error_raises_after_retries(monkeypatch):
    def post(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(api.requests, "post", post)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    try:
        api.post_with_retry("u", "k", max_attempts=2)
        raise AssertionError("should raise")
    except requests.ConnectionError:
        pass


def test_transcribe_file_caches(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake audio")
    n = {"c": 0}

    def post(*a, **k):
        n["c"] += 1
        return FakeResp(200, {"text": " HELLO "})

    monkeypatch.setattr(transcribe, "post_with_retry", post)
    r1 = transcribe.transcribe_file(audio, "en", "k", tmp_path / "cache")
    r2 = transcribe.transcribe_file(audio, "en", "k", tmp_path / "cache")
    assert r1["text"] == "HELLO" and not r1["cached"]
    assert r2["text"] == "HELLO" and r2["cached"]
    assert n["c"] == 1


def test_transcribe_sends_bytes_not_file_handle(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake audio")
    seen = {}

    def post(url, key, files=None, data=None):
        seen["files"] = files
        return FakeResp(200, {"text": "x"})

    monkeypatch.setattr(transcribe, "post_with_retry", post)
    transcribe.transcribe_file(audio, "en", "k")
    assert seen["files"]["file"][1] == b"fake audio"  # reusable across retries


def test_transcribe_file_failure_not_cached(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    monkeypatch.setattr(transcribe, "post_with_retry", lambda *a, **k: FakeResp(415))
    r = transcribe.transcribe_file(audio, "en", "k", tmp_path / "cache")
    assert not r["ok"] and "415" in r["error"]
    assert not list((tmp_path / "cache").glob("*.json")) if (tmp_path / "cache").exists() else True
    json.dumps(r)


def test_out_of_credit_is_not_retried_and_later_calls_fail_fast(monkeypatch):
    monkeypatch.setattr(api, "_exhausted", {})
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    n = {"c": 0}

    def post(*a, **k):
        n["c"] += 1
        r = FakeResp(429)
        r.text = '{"error":{"message":"ExceededBudget: over budget","type":"budget_exceeded","code":"429"}}'
        return r

    monkeypatch.setattr(api.requests, "post", post)
    try:
        api.post_with_retry("https://x/v1/audio/speech", "key-1")
        raise AssertionError("expected BudgetExceeded")
    except api.BudgetExceeded:
        pass
    assert n["c"] == 1                                    # no retries
    try:
        api.post_with_retry("https://x/v1/audio/speech", "key-1")
        raise AssertionError("expected BudgetExceeded")
    except api.BudgetExceeded:
        pass
    assert n["c"] == 1                                    # second call made no request
    assert api.exhausted_services()[0]["url"] == "https://x/v1/audio/speech"
    assert all("key-1" not in str(v) for v in api.exhausted_services())


def test_plain_rate_limit_is_still_retried(monkeypatch):
    monkeypatch.setattr(api, "_exhausted", {})
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    calls = iter([FakeResp(429), FakeResp(200, {"text": "ok"})])
    monkeypatch.setattr(api.requests, "post", lambda *a, **k: next(calls))
    assert api.post_with_retry("u", "key-2").status_code == 200
