"""core.client against the fake backend: key routing, retries, timeouts, concurrency."""
from __future__ import annotations

import threading
import time

import httpx
import pytest

from bodhan_mcp.core import client, config
from bodhan_mcp.core.errors import BodhanError

CHAT = "/v1/chat/completions"


def mt_call():
    return client.post("mt", CHAT, json={"model": "indic-translate", "messages": [{"role": "user", "content": "hi"}]})


@pytest.mark.parametrize(
    "kind,path,model,var",
    [
        ("asr", "/v1/audio/transcriptions", "indic-transcribe", "BODHAN_TRANSCRIBE_KEY"),
        ("mt", CHAT, "indic-translate", "BODHAN_TRANSLATE_KEY"),
        ("ocr", CHAT, "indic-ocr", "BODHAN_OCR_KEY"),
        ("tts", "/v1/audio/speech", "indic-speak", "BODHAN_SPEAK_KEY"),
    ],
)
def test_each_model_uses_its_own_key(bodhan, kind, path, model, var):
    if kind == "asr":
        client.post(kind, path, data={"model": model}, files={"file": ("a.wav", b"RIFF", "audio/wav")})
    else:
        client.post(kind, path, json={"model": model, "messages": [{"role": "user", "content": "x"}], "input": "x"})
    assert [r.key_var for r in bodhan.requests] == [var]


def test_shared_key_is_the_fallback(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"choices": []})

    client.set_transport(httpx.MockTransport(handler))
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY")
    monkeypatch.setenv("BODHAN_API_KEY", "test-key-shared-not-real")
    mt_call()
    assert seen == ["Bearer test-key-shared-not-real"]


def test_missing_key_names_the_variable_and_sends_nothing(bodhan, monkeypatch):
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY")
    with pytest.raises(BodhanError) as exc:
        mt_call()
    err = exc.value
    assert err.code == "AUTH_FAILED"
    assert "BODHAN_TRANSLATE_KEY" in err.hint
    assert "test-key" not in str(err)
    assert bodhan.requests == []


def test_429_honours_retry_after_then_succeeds(bodhan, sleeps):
    bodhan.script("mt", bodhan.error(429, headers={"retry-after": "7"}))
    assert mt_call().status_code == 200
    assert len(bodhan.of("mt")) == 2
    assert 7.0 in sleeps


def test_persistent_429_is_rate_limited_after_three_attempts(bodhan):
    bodhan.script("mt", *[bodhan.error(429, headers={"retry-after": "1"}) for _ in range(5)])
    with pytest.raises(BodhanError) as exc:
        mt_call()
    assert exc.value.code == "RATE_LIMITED" and exc.value.retryable
    assert len(bodhan.of("mt")) == 3


def test_429_credit_without_retry_after_is_budget_exceeded_and_not_retried(bodhan):
    bodhan.script("mt", bodhan.error(429, {"error": {"message": "Insufficient credit"}}))
    with pytest.raises(BodhanError) as exc:
        mt_call()
    assert exc.value.code == "BUDGET_EXCEEDED"
    assert len(bodhan.of("mt")) == 1


def test_one_auth_blip_is_absorbed(bodhan):
    bodhan.script("mt", bodhan.error(401))
    assert mt_call().status_code == 200
    assert len(bodhan.of("mt")) == 2


def test_two_auth_failures_raise(bodhan):
    bodhan.script("mt", bodhan.error(401), bodhan.error(403))
    with pytest.raises(BodhanError) as exc:
        mt_call()
    assert exc.value.code == "AUTH_FAILED"
    assert len(bodhan.of("mt")) == 2


def test_5xx_is_retried(bodhan):
    bodhan.script("mt", bodhan.error(503))
    assert mt_call().status_code == 200
    assert len(bodhan.of("mt")) == 2


def test_persistent_5xx_gives_up_after_three(bodhan):
    bodhan.script("mt", *[bodhan.error(502) for _ in range(5)])
    with pytest.raises(BodhanError) as exc:
        mt_call()
    assert exc.value.code == "UPSTREAM_ERROR"
    assert len(bodhan.of("mt")) == 3


def test_timeout_is_not_retried(bodhan):
    bodhan.script("mt", httpx.ReadTimeout("slow"))
    with pytest.raises(BodhanError) as exc:
        mt_call()
    assert exc.value.code == "TIMEOUT"
    assert len(bodhan.of("mt")) == 1


def test_error_body_inside_a_200_is_an_error(bodhan):
    bodhan.script("mt", httpx.Response(200, json={"error": {"code": "boom", "message": "model crashed"}}))
    with pytest.raises(BodhanError) as exc:
        mt_call()
    assert exc.value.code == "UPSTREAM_ERROR"
    assert "model crashed" in exc.value.message
    assert len(bodhan.of("mt")) == 1


def test_default_concurrency_is_two():
    assert config.max_concurrency() == 2


def test_in_flight_requests_never_exceed_the_cap():
    live = peak = 0
    lock = threading.Lock()

    def handler(request):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return httpx.Response(200, json={"choices": []})

    client.set_transport(httpx.MockTransport(handler))
    threads = [threading.Thread(target=mt_call) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 2
