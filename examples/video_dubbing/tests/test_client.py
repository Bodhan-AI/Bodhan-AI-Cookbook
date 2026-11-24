from __future__ import annotations

import json
import logging

import pytest
import requests

from bodhan_dub.cache import Cache
from bodhan_dub.client import BodhanClient
from bodhan_dub.config import Settings
from bodhan_dub.cost import CostMeter
from bodhan_dub.errors import BodhanAPIError


class FakeResponse:
    def __init__(self, status: int, *, body=None, text: str = "", headers=None, raw: bytes | None = None) -> None:
        self.status_code = status
        self._body = body
        self._raw = raw
        self.text = text
        self.headers = headers or {}
        self.reason = "test"

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def content(self) -> bytes:
        if self._raw is not None:
            return self._raw
        if self._body is not None:
            return json.dumps(self._body).encode("utf-8")
        return self.text.encode("utf-8")

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(tmp_path, session, *, max_attempts: int = 3):
    settings = Settings(
        base_url="https://api.test",
        keys={"transcribe": "test-key", "translate": "test-key", "speak": "test-key", "ocr": "test-key"},
        cache_dir=tmp_path / "cache",
        max_attempts=max_attempts,
        backoff_base_s=0.0,
        timeout_s=5.0,
        max_inflight=2,
        rpm=600_000,
    )
    cost = CostMeter()
    client = BodhanClient(
        settings,
        logging.getLogger("test"),
        Cache(tmp_path / "cache"),
        cost,
        session=session,
        sleep=lambda _seconds: None,
    )
    return client, cost


def test_successful_call_returns_text_and_attempt_count(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, body={"text": "hello world"})])
    client, cost = make_client(tmp_path, session)

    result = client.transcribe(b"RIFF", language="en", audio_seconds=30.0)

    assert result.data["text"] == "hello world"
    assert result.attempts == 1
    assert result.cached is False
    assert cost.total_inr == pytest.approx(0.05, abs=1e-9)
    assert len(session.calls) == 1


def test_request_uses_multipart_with_model_and_language(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, body={"text": "x"})])
    client, _ = make_client(tmp_path, session)

    client.transcribe(b"RIFF", language="hi", audio_seconds=1.0)

    call = session.calls[0]
    assert call["url"].endswith("/v1/audio/transcriptions")
    assert call["data"]["model"] == "indic-transcribe"
    assert call["data"]["language"] == "hi"
    assert "file" in call["files"]
    assert call["headers"]["Authorization"] == "Bearer test-key"


def test_429_is_retried_and_succeeds(tmp_path) -> None:
    session = FakeSession(
        [
            FakeResponse(
                429,
                body={"error": {"message": "rate limited", "code": "rate_limit"}},
                headers={"Retry-After": "0"},
            ),
            FakeResponse(200, body={"text": "second time"}),
        ]
    )
    client, _ = make_client(tmp_path, session)

    result = client.transcribe(b"RIFF", language="en", audio_seconds=1.0)

    assert result.data["text"] == "second time"
    # Throttling is retried without consuming the error budget.
    assert result.attempts == 1
    assert len(session.calls) == 2


def test_transport_error_is_retried(tmp_path) -> None:
    session = FakeSession([requests.ConnectionError("boom"), FakeResponse(200, body={"text": "ok"})])
    client, _ = make_client(tmp_path, session)

    result = client.transcribe(b"RIFF", language="en", audio_seconds=1.0)

    assert result.data["text"] == "ok"
    assert result.attempts == 2


def test_throttling_does_not_consume_the_error_budget(tmp_path) -> None:
    # Five 429s in a row must not exhaust a chunk that allows only two errors.
    body = {"error": {"message": "slow", "code": "rate_limit"}}
    responses = [FakeResponse(429, body=body) for _ in range(5)]
    responses.append(FakeResponse(200, body={"text": "survived"}))
    session = FakeSession(responses)
    client, _ = make_client(tmp_path, session, max_attempts=2)

    result = client.transcribe(b"RIFF", language="en", audio_seconds=1.0)

    assert result.data["text"] == "survived"
    assert result.attempts == 1
    assert len(session.calls) == 6


def test_throttling_is_still_bounded(tmp_path) -> None:
    body = {"error": {"message": "slow", "code": "rate_limit"}}
    session = FakeSession([FakeResponse(429, body=body) for _ in range(50)])
    client, _ = make_client(tmp_path, session)

    with pytest.raises(BodhanAPIError):
        client.transcribe(b"RIFF", language="en", audio_seconds=1.0)


def test_401_is_not_retried_and_carries_the_error_body(tmp_path) -> None:
    session = FakeSession(
        [
            FakeResponse(
                401,
                body={"error": {"message": "bad key", "code": "unauthorized", "request_id": "req-1"}},
            )
        ]
    )
    client, _ = make_client(tmp_path, session)

    with pytest.raises(BodhanAPIError) as caught:
        client.transcribe(b"RIFF", language="en", audio_seconds=1.0)

    assert caught.value.status == 401
    assert caught.value.code == "unauthorized"
    assert caught.value.request_id == "req-1"
    assert caught.value.retryable is False
    assert "bad key" in str(caught.value)
    assert len(session.calls) == 1


def test_retries_are_bounded(tmp_path) -> None:
    body = {"error": {"message": "down", "code": "unavailable"}}
    session = FakeSession([FakeResponse(503, body=body) for _ in range(3)])
    client, _ = make_client(tmp_path, session, max_attempts=3)

    with pytest.raises(BodhanAPIError):
        client.transcribe(b"RIFF", language="en", audio_seconds=1.0)

    assert len(session.calls) == 3


def test_second_identical_call_is_a_cache_hit(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, body={"text": "cached"})])
    client, cost = make_client(tmp_path, session)

    first = client.transcribe(b"RIFF", language="en", audio_seconds=10.0)
    second = client.transcribe(b"RIFF", language="en", audio_seconds=10.0)

    assert first.cached is False
    assert second.cached is True
    assert second.data["text"] == "cached"
    assert len(session.calls) == 1
    assert cost.total_inr == pytest.approx(10.0 * 0.10 / 60.0, abs=1e-6)


def test_a_different_language_does_not_hit_the_cache(tmp_path) -> None:
    session = FakeSession(
        [FakeResponse(200, body={"text": "english"}), FakeResponse(200, body={"text": "hindi"})]
    )
    client, _ = make_client(tmp_path, session)

    client.transcribe(b"RIFF", language="en", audio_seconds=1.0)
    second = client.transcribe(b"RIFF", language="hi", audio_seconds=1.0)

    assert second.cached is False
    assert len(session.calls) == 2


def test_missing_text_field_is_a_clear_error(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, body={"segments": []})])
    client, _ = make_client(tmp_path, session)

    with pytest.raises(BodhanAPIError, match="no 'text' field"):
        client.transcribe(b"RIFF", language="en", audio_seconds=1.0)


def test_non_json_success_is_a_clear_error(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, body=None, text="<html>")])
    client, _ = make_client(tmp_path, session)

    with pytest.raises(BodhanAPIError, match="not JSON"):
        client.transcribe(b"RIFF", language="en", audio_seconds=1.0)


def test_rate_limit_headers_are_captured(tmp_path) -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                body={"text": "x"},
                headers={"x-ratelimit-limit-requests": "60", "x-ratelimit-limit-parallel-requests": "4"},
            )
        ]
    )
    client, _ = make_client(tmp_path, session)

    client.transcribe(b"RIFF", language="en", audio_seconds=1.0)

    limits = client.rate_limits()
    assert limits["indic-transcribe"]["x-ratelimit-limit-requests"] == "60"


def test_429_is_counted_by_the_limiter(tmp_path) -> None:
    session = FakeSession(
        [
            FakeResponse(429, body={"error": {"message": "slow", "code": "rate_limit"}}),
            FakeResponse(200, body={"text": "ok"}),
        ]
    )
    client, _ = make_client(tmp_path, session)

    client.transcribe(b"RIFF", language="en", audio_seconds=1.0)

    assert client.limiter_stats()["indic-transcribe"]["rejections"] == 1


def test_advertised_request_limit_becomes_the_ceiling(tmp_path) -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                body={"text": "x"},
                headers={"x-ratelimit-limit-requests": "60"},
            )
        ]
    )
    client, _ = make_client(tmp_path, session)

    client.transcribe(b"RIFF", language="en", audio_seconds=1.0)

    assert client.limiter_stats()["indic-transcribe"]["rpm_ceiling"] == 60


# -- translation transports ------------------------------------------------


def _chat_response(text: str, tokens: int = 7, finish: str = "stop") -> FakeResponse:
    return FakeResponse(
        200,
        body={
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": tokens, "total_tokens": 20 + tokens},
        },
    )


def test_translate_uses_the_documented_endpoint_when_available(tmp_path) -> None:
    session = FakeSession(
        [FakeResponse(200, body={"translation": "नमस्ते", "usage": {"completion_tokens": 5}})]
    )
    client, cost = make_client(tmp_path, session)

    result = client.translate("hello", target_language="hi")

    assert result.data["translation"] == "नमस्ते"
    assert result.data["output_tokens"] == 5
    assert session.calls[0]["url"].endswith("/translate")
    assert session.calls[0]["json"] == {"text": "hello", "target_language": "hi"}
    assert cost.total_inr == pytest.approx(5 * 0.20 / 10_000)


def test_translate_falls_back_to_chat_completions_on_404(tmp_path) -> None:
    session = FakeSession(
        [
            FakeResponse(404, body={"detail": "Not Found"}),
            _chat_response("नमस्ते"),
            _chat_response("दूसरा"),
        ]
    )
    client, _ = make_client(tmp_path, session)

    result = client.translate("hello", target_language="hi")

    assert result.data["translation"] == "नमस्ते"
    assert session.calls[0]["url"].endswith("/translate")
    assert session.calls[1]["url"].endswith("/v1/chat/completions")

    # The transport is latched: the next call skips the missing endpoint.
    client.translate("another", target_language="hi")
    assert session.calls[2]["url"].endswith("/v1/chat/completions")
    assert len(session.calls) == 3


def test_chat_transport_sends_the_documented_prompt(tmp_path) -> None:
    session = FakeSession([FakeResponse(404, body={"detail": "Not Found"}), _chat_response("ok")])
    client, _ = make_client(tmp_path, session)

    client.translate("hello", target_language="ta", script="codemix")

    body = session.calls[1]["json"]
    assert body["model"] == "indic-translate"
    assert body["messages"] == [
        {
            "role": "user",
            "content": "Translate the following text into Tamil (code-mixed):\n\nhello",
        }
    ]
    # Generation settings are rejected by the server, so none are sent.
    assert "target_language" not in body
    assert "max_tokens" not in body


def test_truncated_chat_output_is_flagged(tmp_path) -> None:
    session = FakeSession(
        [FakeResponse(404, body={"detail": "Not Found"}), _chat_response("incomplete", tokens=4, finish="length")]
    )
    client, _ = make_client(tmp_path, session)

    result = client.translate("a long sentence", target_language="hi")

    assert result.data["truncated"] is True


def test_chat_usage_tokens_drive_the_cost(tmp_path) -> None:
    session = FakeSession(
        [
            FakeResponse(404, body={"detail": "Not Found"}),
            _chat_response("translated text", tokens=42),
        ]
    )
    client, cost = make_client(tmp_path, session)

    client.translate("hello", target_language="hi")

    assert cost.total_inr == pytest.approx(42 * 0.20 / 10_000)


def test_a_malformed_chat_response_is_a_clear_error(tmp_path) -> None:
    session = FakeSession(
        [FakeResponse(404, body={"detail": "Not Found"}), FakeResponse(200, body={"choices": []})]
    )
    client, _ = make_client(tmp_path, session)

    with pytest.raises(BodhanAPIError, match="choices"):
        client.translate("hello", target_language="hi")


def test_translation_is_cached(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, body={"translation": "नमस्ते"})])
    client, cost = make_client(tmp_path, session)

    first = client.translate("hello", target_language="hi")
    second = client.translate("hello", target_language="hi")

    assert first.cached is False
    assert second.cached is True
    assert len(session.calls) == 1
    assert second.data["translation"] == "नमस्ते"


def test_an_empty_translation_is_not_silently_accepted(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, body={"translation": ""})])
    client, _ = make_client(tmp_path, session)

    result = client.translate("hello", target_language="hi")

    assert result.data["translation"] == ""


# -- speech ---------------------------------------------------------------


def test_speak_returns_audio_bytes_and_charges_per_character(tmp_path) -> None:
    audio = b"RIFF....WAVEfmt "
    session = FakeSession(
        [FakeResponse(200, raw=audio, headers={"content-type": "audio/wav"})]
    )
    client, cost = make_client(tmp_path, session)

    result = client.speak("नमस्ते दुनिया", voice="Kavya", language="hi")

    assert result.audio == audio
    assert result.content_type == "audio/wav"
    assert cost.total_inr == pytest.approx(len("नमस्ते दुनिया") * 6.0 / 10_000)
    body = session.calls[0]["json"]
    assert body["model"] == "indic-speak"
    assert body["voice"] == "Kavya"
    assert json.loads(body["instructions"]) == {"lang": "hi"}


def test_speak_sends_the_style(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, raw=b"RIFF")])
    client, _ = make_client(tmp_path, session)

    client.speak("नमस्ते", voice="Kavya", language="hi", style="educational lecture")

    body = session.calls[0]["json"]
    assert json.loads(body["instructions"])["style"] == "educational lecture"


def test_speak_is_cached_and_a_second_call_is_free(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, raw=b"RIFFaudio")])
    client, cost = make_client(tmp_path, session)

    first = client.speak("नमस्ते", voice="Kavya", language="hi")
    second = client.speak("नमस्ते", voice="Kavya", language="hi")

    assert first.cached is False
    assert second.cached is True
    assert second.audio == b"RIFFaudio"
    assert len(session.calls) == 1
    assert cost.total_inr == pytest.approx(len("नमस्ते") * 6.0 / 10_000)


def test_speak_rejects_an_empty_response(tmp_path) -> None:
    session = FakeSession([FakeResponse(200, raw=b"")])
    client, _ = make_client(tmp_path, session)

    with pytest.raises(BodhanAPIError, match="no audio"):
        client.speak("hello", voice="Kavya", language="en")
