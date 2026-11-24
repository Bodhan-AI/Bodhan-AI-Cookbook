"""Shared client: key fallback, retries/backoff, rate-limit handling, caching, cost accounting."""
from __future__ import annotations

import json

import pytest
from conftest import tone_wav

from bodhan_mcp import config, pricing
from bodhan_mcp.client import client, ledger
from bodhan_mcp.errors import ToolFailure


def test_official_key_names_win_and_fallback_names_work(monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "official")
    monkeypatch.setenv("INDIC_TRANSLATE", "fallback")
    assert config.get_key("indic-translate") == "official"
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY")
    assert config.get_key("indic-translate") == "fallback"
    for model, var in (("indic-transcribe", "INDIC_TRANSCRIBE_API_KEY"), ("indic-speak", "INDIC_SPEAK"), ("indic-ocr", "INDIC_OCR")):
        for official in config.KEY_ENV[model][:1]:
            monkeypatch.delenv(official)
        monkeypatch.setenv(var, "fb-" + model)
        assert config.get_key(model) == "fb-" + model


def test_missing_key_is_structured_error(monkeypatch):
    monkeypatch.delenv("BODHAN_OCR_KEY")
    with pytest.raises(ToolFailure) as e:
        config.get_key("indic-ocr")
    assert e.value.info.error_code == "MISSING_API_KEY" and "BODHAN_OCR_KEY" in e.value.info.hint


def test_retries_with_backoff_then_succeeds(bodhan):
    sleeps = []
    client.sleep = sleeps.append
    bodhan.fail_first["/v1/chat/completions"] = [503, 502]
    text, trunc, info = client.translate("Hello", "hi")
    assert text and not trunc and len(bodhan.calls) == 3
    assert sleeps and sleeps[1] > sleeps[0] - 0.3  # exponential growth (plus jitter)


def test_gives_up_after_max_attempts_with_retryable_error(bodhan):
    bodhan.fail_first["/v1/chat/completions"] = [500] * 10
    with pytest.raises(ToolFailure) as e:
        client.translate("Hello", "hi")
    assert e.value.info.retryable and len(bodhan.calls) == 4


def test_429_is_not_hammered_and_marked_retryable(bodhan):
    bodhan.fail_first["/v1/chat/completions"] = [429] * 10
    with pytest.raises(ToolFailure) as e:
        client.translate("Hello", "hi")
    assert e.value.info.error_code == "RATE_LIMITED" and e.value.info.retryable and len(bodhan.calls) == 2


def test_auth_error_not_retried(bodhan):
    bodhan.fail_first["/v1/chat/completions"] = [401]
    with pytest.raises(ToolFailure) as e:
        client.translate("Hello", "hi")
    assert e.value.info.error_code == "AUTH_FAILED" and not e.value.info.retryable and len(bodhan.calls) == 1


def test_cache_key_is_model_and_exact_input(bodhan):
    client.translate("Hello", "hi")
    client.translate("Hello", "hi")
    assert len(bodhan.calls) == 1
    client.translate("Hello", "ta")      # different target = different input
    client.translate("Hello!", "hi")     # different text
    assert len(bodhan.calls) == 3


def test_cost_accounting_matches_published_prices(tmp_path, bodhan):
    _, _, tr = client.translate("Hello", "hi")
    assert tr.cost_inr == pytest.approx(10 / 10000 * 0.20)      # 10 output tokens in the mock
    _, sp = client.speak("x" * 100, "hi", "Amit")
    assert sp.cost_inr == pytest.approx(100 / 10000 * 6)
    wav = tone_wav(tmp_path / "a.wav", [(30, True)])
    _, asr = client.transcribe(wav, 30.0, "hi")
    assert asr.cost_inr == pytest.approx(0.05)
    _, oc = client.ocr_page(b"img", "image/png")
    assert oc.cost_inr == pytest.approx(0.20)
    assert ledger.total_cost >= 0.2 + 0.05
    assert pricing.speak_cost(10_000) == 6.0 and pricing.transcribe_cost(60) == 0.10


def test_concurrency_limit_is_bounded_semaphore(monkeypatch):
    monkeypatch.setenv("BODHAN_MAX_CONCURRENCY", "2")
    client._sem = None
    assert client.sem._value == 2


def test_translate_request_shape_uses_documented_chat_route(bodhan):
    client.translate("Hello", "ta", script="roman")
    body = bodhan.calls[0]["json"]
    assert bodhan.calls[0]["path"] == "/v1/chat/completions"
    assert body == {"model": "indic-translate", "messages": [{"role": "user", "content": "Hello"}], "target_language_code": "ta", "target_script": "roman"}
    client.translate("Hello", "en")
    assert "target_script" not in bodhan.calls[-1]["json"]
    json.dumps(body)
