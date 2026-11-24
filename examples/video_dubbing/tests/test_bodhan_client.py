"""BodhanClient behaviour against a fake HTTP session: retries, errors, cost metering."""

import json

import pytest
import requests
from fakes import FakeSession, make_wav, response

from bodhan_dub.bodhan import BodhanClient, BodhanError
from bodhan_dub.config import Settings
from bodhan_dub.llm import LLMError, extract_json

SETTINGS = Settings.from_env({"BODHAN_API_KEY": "test-key", "LLM_BASE_URL": "http://x", "LLM_MODEL": "m",
                              "LLM_API_KEY": "k"})


def client_with(*responses) -> tuple[BodhanClient, FakeSession]:
    session = FakeSession(*responses)
    return BodhanClient(SETTINGS, session=session, backoff_initial_s=0, backoff_max_s=0), session


def test_retries_rate_limit_then_succeeds():
    ok = {"translation": "नमस्ते", "truncated": False, "usage": {"completion_tokens": 10_000}}
    rate_limited = {"error": {"code": "rate_limit_exceeded_error", "message": "slow down"}}
    client, session = client_with(response(429, rate_limited), requests.ConnectionError("reset"), response(200, ok))
    result = client.translate("Hello", "hi")
    assert result.text == "नमस्ते"
    assert len(session.calls) == 3
    usage = client.usage.snapshot()
    assert usage["retries"]["indic-translate"] == 2
    assert usage["cost_inr"]["indic-translate"] == pytest.approx(0.20)


def test_client_error_is_not_retried():
    client, session = client_with(response(400, {"error": {"code": "unsupported_language", "message": "no"}}))
    with pytest.raises(BodhanError) as info:
        client.translate("Hello", "xx")
    assert info.value.code == "unsupported_language" and not info.value.retryable
    assert len(session.calls) == 1


def test_gives_up_after_max_attempts():
    client, session = client_with(*[response(503, {}) for _ in range(8)])
    with pytest.raises(BodhanError) as info:
        client.translate("Hello", "hi")
    assert info.value.status == 503
    assert len(session.calls) == 8


def test_rate_limit_sets_a_shared_cooldown_that_grows_and_resets():
    client = BodhanClient(SETTINGS, session=FakeSession(), backoff_initial_s=1.0, backoff_max_s=4.0)
    assert client._on_rate_limited("indic-transcribe", None) == 1.0
    assert client._on_rate_limited("indic-transcribe", None) == 2.0
    assert client._on_rate_limited("indic-transcribe", None) == 4.0
    assert client._on_rate_limited("indic-transcribe", None) == 4.0  # capped at backoff_max_s
    assert client._on_rate_limited("indic-transcribe", "7") == 7.0  # Retry-After wins when sent
    assert client._cooldown_until["indic-speak"] == 0.0  # other models are unaffected
    client._on_success("indic-transcribe")
    assert client._on_rate_limited("indic-transcribe", None) == 1.0


def test_translate_falls_back_to_chat_transport_on_404_and_sticks():
    chat_ok = {"choices": [{"message": {"content": " नमस्ते "}, "finish_reason": "stop"}],
               "usage": {"completion_tokens": 3}}
    chat_cut = {"choices": [{"message": {"content": "आधा"}, "finish_reason": "length"}],
                "usage": {"completion_tokens": 1}}
    client, session = client_with(response(404, {"detail": "Not Found"}), response(200, chat_ok),
                                  response(200, chat_cut))
    first = client.translate("Hello", "hi", script="codemix")
    second = client.translate("Hello again", "ta")
    urls = [url for url, _ in session.calls]
    assert urls == ["https://api.bodhan.ai/translate", "https://api.bodhan.ai/v1/chat/completions",
                    "https://api.bodhan.ai/v1/chat/completions"]  # /translate is not retried after the 404
    assert session.calls[1][1]["json"]["messages"] == [
        {"role": "user", "content": "Translate to Hindi (code-mixed): Hello"}]
    assert session.calls[2][1]["json"]["messages"][0]["content"] == "Translate to Tamil: Hello again"
    assert (first.text, first.truncated) == ("नमस्ते", False)
    assert second.truncated is True


def test_transcribe_meters_audio_seconds():
    client, session = client_with(response(200, {"text": " Hello there. "}))
    assert client.transcribe(make_wav(30.0)) == "Hello there."
    assert session.calls[0][0] == "https://api.bodhan.ai/v1/audio/transcriptions"
    assert client.usage.snapshot()["cost_inr"]["indic-transcribe"] == pytest.approx(0.05)


def test_speak_sends_instructions_as_json_string_and_checks_wav():
    client, session = client_with(response(200, content=make_wav(1.0)))
    audio = client.speak("नमस्ते", voice="Kavya", lang="hi", style="educational lecture")
    assert audio.startswith(b"RIFF")
    body = session.calls[0][1]["json"]
    assert json.loads(body["instructions"]) == {"lang": "hi", "style": "educational lecture"}

    client, _ = client_with(response(200, content=b"<html>oops</html>"))
    with pytest.raises(BodhanError, match="expected WAV"):
        client.speak("x", voice="Kavya", lang="hi")


def test_extract_json_tolerates_fences_and_chatter():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"ok": true} hope that helps') == {"ok": True}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_spent_credit_fails_fast_instead_of_waiting_like_a_rate_limit():
    body = {"error": {"code": "429", "message": "ExceededBudget: User=x over budget. Spend=10.03, Budget=10.0"}}
    client, session = client_with(response(429, body))
    with pytest.raises(BodhanError) as info:
        client.translate("Hello", "hi")
    assert info.value.code == "budget_exceeded" and not info.value.retryable
    assert "credit exhausted" in str(info.value)
    assert len(session.calls) == 1  # no retries, no cooldown
    assert client.usage.snapshot()["rate_limit_wait_s"] == {}
