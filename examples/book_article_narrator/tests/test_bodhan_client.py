import json

import pytest
import requests
from narrator import config
from narrator.bodhan_client import BodhanClient
from narrator.errors import BodhanAPIError, MissingKeyError

from tests.helpers import make_tone


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.headers = headers or {}
        self.text = content.decode(errors="replace") if content else ""

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class FakeSession:
    """Stands in for requests: hands back the replies we queue, records the calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def post(self, url, headers=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout, **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_client(*replies):
    sleeps = []
    session = FakeSession(*replies)
    return BodhanClient(session=session, sleep=sleeps.append), session, sleeps


def good_translation(text="नमस्ते", truncated=False):
    finish = "length" if truncated else "stop"
    body = {"choices": [{"message": {"content": text}, "finish_reason": finish}]}
    return FakeResponse(200, body)


def error_reply(status, code, message="problem", request_id="req-1", headers=None):
    body = {"error": {"message": message, "code": code, "request_id": request_id}}
    return FakeResponse(status, body, headers=headers)


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for name in ("BODHAN_API_KEY", "BODHAN_TRANSLATE_API_KEY", "BODHAN_SPEAK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BODHAN_TRANSLATE_API_KEY", "translate-key")
    monkeypatch.setenv("BODHAN_SPEAK_API_KEY", "speak-key")


# --- translate ---------------------------------------------------------------


def test_translate_sends_the_request_that_works():
    client, session, _ = make_client(good_translation())
    assert client.translate("Hello.", "hi") == ("नमस्ते", False)
    call = session.calls[0]
    assert call["url"] == config.BASE_URL + "/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer translate-key"}
    assert call["json"] == {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "Hello."}],
        "target_language_code": "hi",
    }
    assert call["timeout"] == (10, 60)


def test_translate_reports_a_truncated_translation():
    client, _, _ = make_client(good_translation(truncated=True))
    assert client.translate("Hello.", "hi")[1] is True


def test_translate_treats_any_unfinished_reply_as_truncated():
    body = {"choices": [{"message": {"content": "आधा"}, "finish_reason": "content_filter"}]}
    client, _, _ = make_client(FakeResponse(200, body))
    assert client.translate("Hello.", "hi")[1] is True


@pytest.mark.parametrize(
    "reply",
    [
        FakeResponse(200, None, content=b"<html>"),
        FakeResponse(200, {"choices": []}),
        FakeResponse(200, {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}),
    ],
)
def test_translate_unreadable_reply_is_an_error(reply):
    client, _, _ = make_client(reply)
    with pytest.raises(BodhanAPIError):
        client.translate("Hello.", "hi")


# --- speak -------------------------------------------------------------------


def test_speak_sends_the_documented_request_and_returns_wav():
    wav = make_tone(0.1)
    client, session, _ = make_client(FakeResponse(200, content=wav))
    assert client.speak("Namaste.", "Kavya", "hi") == wav
    call = session.calls[0]
    assert call["url"] == config.BASE_URL + "/v1/audio/speech"
    assert call["headers"] == {"Authorization": "Bearer speak-key"}
    assert call["json"]["model"] == "indic-speak"
    assert call["json"]["input"] == "Namaste."
    assert call["json"]["voice"] == "Kavya"
    assert json.loads(call["json"]["instructions"]) == {"lang": "hi"}


def test_speak_passes_the_style_when_given():
    client, session, _ = make_client(FakeResponse(200, content=make_tone(0.1)))
    client.speak("Namaste.", "Kavya", "hi", "news")
    assert json.loads(session.calls[0]["json"]["instructions"]) == {"lang": "hi", "style": "news"}


def test_speak_reply_that_is_not_audio_is_an_error():
    client, _, _ = make_client(FakeResponse(200, content=b"not audio"))
    with pytest.raises(BodhanAPIError):
        client.speak("Namaste.", "Kavya", "hi")


def test_422_from_speech_is_not_retried_and_keeps_bodhan_detail():
    client, session, sleeps = make_client(error_reply(422, None, "unknown voice 'Zed'"))
    with pytest.raises(BodhanAPIError) as error:
        client.speak("Namaste.", "Zed", "hi")
    assert len(session.calls) == 1 and sleeps == []
    assert error.value.status == 422
    assert "unknown voice" in error.value.detail


# --- keys --------------------------------------------------------------------


def test_missing_key_makes_no_call(monkeypatch):
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY")
    client, session, _ = make_client(good_translation())
    with pytest.raises(MissingKeyError):
        client.translate("Hello.", "hi")
    assert session.calls == []


def test_shared_key_is_used_when_there_is_no_separate_one(monkeypatch):
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY")
    monkeypatch.setenv("BODHAN_API_KEY", "shared-key")
    client, session, _ = make_client(good_translation())
    client.translate("Hello.", "hi")
    assert session.calls[0]["headers"] == {"Authorization": "Bearer shared-key"}


# --- retries -----------------------------------------------------------------


def test_429_then_success_waits_and_retries():
    client, session, sleeps = make_client(
        error_reply(429, "rate_limit_exceeded_error"), good_translation()
    )
    assert client.translate("Hello.", "hi")[0] == "नमस्ते"
    assert len(session.calls) == 2 and sleeps == [1]


def test_429_uses_the_retry_after_header():
    client, _, sleeps = make_client(
        error_reply(429, "rate_limit_exceeded_error", headers={"retry-after": "3"}),
        good_translation(),
    )
    client.translate("Hello.", "hi")
    assert sleeps == [3.0]


def test_429_three_times_gives_up_without_looping():
    client, session, sleeps = make_client(*[error_reply(429, "rate_limit_exceeded_error")] * 3)
    with pytest.raises(BodhanAPIError) as error:
        client.translate("Hello.", "hi")
    assert len(session.calls) == 3 and sleeps == [1, 2]
    assert error.value.code == "rate_limit_exceeded_error"
    assert error.value.request_id == "req-1"


def test_500_is_retried():
    client, session, _ = make_client(error_reply(500, "internal_error"), good_translation())
    client.translate("Hello.", "hi")
    assert len(session.calls) == 2


def test_network_error_is_retried_then_reported():
    client, session, sleeps = make_client(requests.ConnectionError(), good_translation())
    client.translate("Hello.", "hi")
    assert len(session.calls) == 2 and sleeps == [1]

    client, session, _ = make_client(*[requests.Timeout()] * 3)
    with pytest.raises(BodhanAPIError) as error:
        client.translate("Hello.", "hi")
    assert error.value.code == "connection_error" and len(session.calls) == 3


def test_401_is_not_retried_for_a_key_that_never_worked():
    client, session, sleeps = make_client(error_reply(401, "invalid_api_key_error"))
    with pytest.raises(BodhanAPIError) as error:
        client.translate("Hello.", "hi")
    assert len(session.calls) == 1 and sleeps == []
    assert ".env" in str(error.value)


def test_401_is_retried_once_the_key_has_worked_before():
    client, session, _ = make_client(
        good_translation(), error_reply(401, "invalid_api_key_error"), good_translation()
    )
    client.translate("Hello.", "hi")
    client.translate("Hello again.", "hi")
    assert len(session.calls) == 3


# --- other errors ------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "unsupported_language"), (413, "payload_too_large")],
)
def test_client_errors_are_not_retried(status, code):
    client, session, sleeps = make_client(error_reply(status, code, "details here"))
    with pytest.raises(BodhanAPIError) as error:
        client.translate("Hello.", "hi")
    assert len(session.calls) == 1 and sleeps == []
    assert error.value.status == status
    assert error.value.code == code
    assert error.value.detail == "details here"


def test_request_id_falls_back_to_the_header():
    client, _, _ = make_client(FakeResponse(400, {"oops": 1}, headers={"x-request-id": "hdr-9"}))
    with pytest.raises(BodhanAPIError) as error:
        client.translate("Hello.", "hi")
    assert error.value.request_id == "hdr-9"


def test_error_body_without_bodhans_envelope_is_kept_as_detail():
    client, _, _ = make_client(FakeResponse(404, {"detail": "Not Found"}))
    with pytest.raises(BodhanAPIError) as error:
        client.translate("Hello.", "hi")
    assert error.value.status == 404
    assert error.value.detail == "Not Found"