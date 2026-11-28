import json

import pytest
import requests
from conftest import FakeResponse, FakeSession, make_wav
from feedback_analyser.bodhan import MODEL_KEY_VARS, BodhanClient, BodhanError, markdown_to_text
from feedback_analyser.cache import DiskCache, cache_key


def chat(content, finish="stop"):
    return FakeResponse(payload={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def client_with(responder, **kwargs):
    session = FakeSession(responder)
    waits = []
    client = BodhanClient(api_key="test-key", session_factory=lambda: session, sleep=waits.append, **kwargs)
    return client, session, waits


KEY_VARS = ["BODHAN_API_KEY", *MODEL_KEY_VARS.values()]


def test_missing_key_is_reported(monkeypatch):
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BodhanError, match="BODHAN_API_KEY"):
        BodhanClient()


def test_each_model_uses_its_own_key_and_falls_back_to_the_shared_one(monkeypatch):
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BODHAN_API_KEY", "shared")
    monkeypatch.setenv("BODHAN_TRANSLATE_API_KEY", "translate-only")
    session = FakeSession(lambda url, kw: FakeResponse(payload={"translation": "x", "transliteration": "y"}))
    client = BodhanClient(session_factory=lambda: session)
    client.translate("Hello", "hi")
    client.transliterate("नमस्ते", "hi")
    sent = [kwargs["headers"]["Authorization"] for _, kwargs in session.requests]
    assert sent == ["Bearer translate-only", "Bearer shared"]


def test_model_without_any_key_names_the_variable_to_set(monkeypatch):
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BODHAN_TRANSLATE_API_KEY", "translate-only")
    client = BodhanClient(session_factory=lambda: FakeSession(lambda url, kw: FakeResponse(payload={})))
    with pytest.raises(BodhanError, match="BODHAN_OCR_API_KEY"):
        client._key_for("indic-ocr")


def test_translate_uses_plain_route_and_sends_bearer_key():
    client, session, _ = client_with(lambda url, kw: FakeResponse(payload={"translation": " नमस्ते ", "truncated": False}))
    assert client.translate("Hello", "hi") == "नमस्ते"
    url, kwargs = session.requests[0]
    assert url == "https://api.bodhan.ai/translate"
    assert kwargs["headers"] == {"Authorization": "Bearer test-key"}
    assert kwargs["json"] == {"text": "Hello", "target_language": "hi"}


def test_falls_back_to_chat_route_on_404_and_remembers():
    def responder(url, kw):
        if url.endswith("/translate") or url.endswith("/transliterate"):
            return FakeResponse(status=404, payload={"detail": "Not Found"})
        return chat("ठीक है")

    client, session, _ = client_with(responder)
    assert client.translate("Okay", "hi", script="native") == "ठीक है"
    assert client.transliterate("ठीक है", "hi") == "ठीक है"
    urls = [url.rsplit("/", 1)[-1] for url, _ in session.requests]
    assert urls == ["translate", "completions", "completions"]
    body = session.requests[1][1]["json"]
    assert body["model"] == "indic-translate" and body["target_language"] == "hi" and body["script"] == "native"
    body = session.requests[2][1]["json"]
    assert body["model"] == "indic-transliterate" and body["target_language"] == "hi" and body["script"] == "roman"


def test_retries_429_using_retry_after_then_succeeds():
    replies = [FakeResponse(status=429, payload={"error": {"message": "slow down"}}, headers={"retry-after": "7"}), chat("ok")]
    client, session, waits = client_with(lambda url, kw: replies.pop(0))
    client._plain_routes = False
    assert client.translate("x", "hi") == "ok"
    assert waits == [7.0]
    assert client.calls["indic-translate"] == 1


def test_gives_up_after_bounded_attempts_with_readable_error():
    error = {"error": {"message": "budget exceeded", "code": "rate_limit_exceeded_error", "request_id": "req_1"}}
    client, session, waits = client_with(lambda url, kw: FakeResponse(status=429, payload=error))
    client._plain_routes = False
    with pytest.raises(BodhanError) as caught:
        client.translate("x", "hi")
    assert len(session.requests) == 4 and len(waits) == 3
    assert "budget exceeded" in str(caught.value) and "req_1" in str(caught.value) and caught.value.status == 429


def test_client_errors_are_not_retried():
    client, session, _ = client_with(lambda url, kw: FakeResponse(status=401, payload={"error": {"message": "bad key"}}))
    with pytest.raises(BodhanError, match="bad key"):
        client.speak("नमस्ते", "Kavya", "hi")
    assert len(session.requests) == 1


def test_network_errors_are_retried_then_raised():
    client, session, waits = client_with(lambda url, kw: requests.ConnectionError("down"))
    with pytest.raises(BodhanError, match="could not reach"):
        client.speak("नमस्ते", "Kavya", "hi")
    assert len(session.requests) == 4


def test_truncated_translation_is_an_error_not_a_silent_partial():
    client, _, _ = client_with(lambda url, kw: chat("half a sent", finish="length"))
    client._plain_routes = False
    with pytest.raises(BodhanError, match="cut off"):
        client.translate("a long text", "hi")


def test_cache_answers_second_call_without_network(tmp_path):
    client, session, _ = client_with(lambda url, kw: FakeResponse(payload={"translation": "नमस्ते"}), cache=DiskCache(tmp_path))
    assert client.translate("Hello", "hi") == client.translate("Hello", "hi") == "नमस्ते"
    assert len(session.requests) == 1
    assert client.calls["indic-translate"] == 1 and client.cache_hits["indic-translate"] == 1
    assert client.translate("Hello", "ta") and len(session.requests) == 2


def test_cache_key_separates_fields():
    assert cache_key("ab", "c") != cache_key("a", "bc")
    assert cache_key("x", None) == cache_key("x", None)


def test_long_wav_is_split_into_requests_under_the_limit(tmp_path):
    clip = tmp_path / "note.wav"
    clip.write_bytes(make_wav(60))
    texts = iter(["एक", "दो", "तीन"])
    client, session, _ = client_with(lambda url, kw: FakeResponse(payload={"text": next(texts)}))
    assert client.transcribe(clip, "hi") == "एक दो तीन"
    assert len(session.requests) == 3
    assert session.requests[0][1]["data"] == {"model": "indic-transcribe", "language": "hi"}


def test_short_wav_is_one_request_without_language_hint(tmp_path):
    clip = tmp_path / "note.wav"
    clip.write_bytes(make_wav(5))
    client, session, _ = client_with(lambda url, kw: FakeResponse(payload={"text": "ठीक"}))
    assert client.transcribe(clip) == "ठीक"
    assert session.requests[0][1]["data"] == {"model": "indic-transcribe"}


def test_ocr_sends_data_url_and_averages_block_confidence(tmp_path):
    card = tmp_path / "card.png"
    card.write_bytes(b"\x89PNG fake")
    payload = {"choices": [{"message": {"content": "# सुझाव\n\n**बिल** गलत था"}}], "blocks": [{"conf": 0.9}, {"conf": 0.5}]}
    client, session, _ = client_with(lambda url, kw: FakeResponse(payload=payload))
    assert client.ocr(card) == {"text": "सुझाव\nबिल गलत था", "confidence": 0.7}
    part = session.requests[0][1]["json"]["messages"][0]["content"][0]
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


def test_speak_sends_instructions_as_json_string_and_rejects_non_audio():
    client, session, _ = client_with(lambda url, kw: FakeResponse(content=make_wav(0.1)))
    assert client.speak("नमस्ते", "Kavya", "hi", "Customer Care")[:4] == b"RIFF"
    body = session.requests[0][1]["json"]
    assert json.loads(body["instructions"]) == {"lang": "hi", "style": "Customer Care"} and body["voice"] == "Kavya"
    broken, _, _ = client_with(lambda url, kw: FakeResponse(content=b"<html>"))
    with pytest.raises(BodhanError, match="WAV"):
        broken.speak("नमस्ते", "Kavya", "hi")


def test_markdown_to_text_strips_markup_and_tables():
    assert markdown_to_text("## Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n<br>**bold**") == "Title\na b\n1 2\nbold"
