
import base64
import email.utils
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO / "examples" / "bodhan_tool_agent"
sys.path.insert(0, str(PACKAGE_ROOT))

import bodhan_tool_agent.bodhan_client as client_module  # noqa: E402
from bodhan_tool_agent.bodhan_client import (  # noqa: E402
    DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS,
    BodhanAPIError,
    BodhanClient,
    BodhanDeadlineExceeded,
    BodhanNetworkError,
    BodhanResponseError,
    BodhanUncertainOutcomeError,
    TranslationChunkError,
    TranslationTruncatedError,
)
from bodhan_tool_agent.config import ConfigurationError, Settings  # noqa: E402
from bodhan_tool_agent.text import (  # noqa: E402
    TextChunkingError,
    split_text,
)


class MockSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def response(status=200, payload=None, content=b"", headers=None, reason=None):
    result = requests.Response()
    result.status_code = status
    result._content = content
    result.headers.update(headers or {})
    result.reason = reason
    if payload is not None:
        result._content = json.dumps(payload).encode("utf-8")
        result.headers["Content-Type"] = "application/json"
    return result


@pytest.fixture
def settings(monkeypatch):
    for name in (
        "BODHAN_API_KEY",
        "BODHAN_TRANSCRIBE_KEY",
        "BODHAN_TRANSLATE_KEY",
        "BODHAN_SPEAK_KEY",
        "BODHAN_OCR_KEY",
        "BODHAN_BASE_URL",
        "BODHAN_TIMEOUT",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("BODHAN_API_KEY", "fallback-secret")
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "transcribe-secret")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "translate-secret")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "speak-secret")
    monkeypatch.setenv("BODHAN_OCR_KEY", "ocr-secret")
    return Settings.from_env(load_dotenv_file=False)


def test_config_key_priority_and_fallback(monkeypatch):
    for name in (
        "BODHAN_API_KEY",
        "BODHAN_TRANSCRIBE_KEY",
        "BODHAN_TRANSLATE_KEY",
        "BODHAN_SPEAK_KEY",
        "BODHAN_OCR_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("BODHAN_API_KEY", "fallback")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "translate-only")
    config = Settings.from_env(load_dotenv_file=False)

    assert config.key_for("indic-transcribe") == "fallback"
    assert config.key_for("indic-translate") == "translate-only"


def test_config_rejects_invalid_timeout(monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "test-key")
    monkeypatch.setenv("BODHAN_TIMEOUT", "not-a-number")

    with pytest.raises(ConfigurationError):
        Settings.from_env(load_dotenv_file=False)


def test_transcribe_endpoint_method_file_and_key(tmp_path, settings):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFFtest")
    session = MockSession([response(payload={"text": "नमस्ते"})])
    client = BodhanClient(settings, session=session)

    assert client.transcribe(audio, language="hi") == "नमस्ते"

    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.bodhan.ai/v1/audio/transcriptions"
    assert call["headers"]["Authorization"] == "Bearer transcribe-secret"
    assert call["data"] == {"model": "indic-transcribe", "language": "hi"}
    assert call["timeout"] == 60.0


def test_transcribe_client_rejects_audio_over_limit(tmp_path, settings, monkeypatch):
    audio = tmp_path / "long.wav"
    audio.write_bytes(b"RIFFtest")
    monkeypatch.setattr(
        client_module,
        "audio_duration_seconds",
        lambda path: 30.1,
    )
    client = BodhanClient(settings, session=MockSession([]))

    with pytest.raises(BodhanResponseError, match="30-second"):
        client.transcribe(audio)


def test_translate_endpoint_payload_and_key(settings):
    session = MockSession([
        response(payload={
            "translation": "नमस्ते",
            "target_language": "hi",
            "script": "native",
            "truncated": False,
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        })
    ])
    client = BodhanClient(settings, session=session)

    result = client.translate("Hello", "hi", script="native", max_output_tokens=20)
    call = session.calls[0]

    assert call["method"] == "POST"
    assert call["url"] == "https://api.bodhan.ai/translate"
    assert call["headers"]["Authorization"] == "Bearer translate-secret"
    assert call["json"] == {
        "text": "Hello",
        "target_language": "hi",
        "script": "native",
        "max_output_tokens": 20,
    }
    assert result["translation"] == "नमस्ते"
    assert result["truncated"] is False
    assert result["usage"]["total_tokens"] == 5


@pytest.mark.parametrize("max_output_tokens", [None, pytest.param("omitted", id="omitted")])
def test_translate_applies_shared_default_output_limit(settings, max_output_tokens):
    session = MockSession([
        response(payload={
            "translation": "नमस्ते",
            "target_language": "hi",
            "truncated": False,
            "usage": {"completion_tokens": 3},
        })
    ])
    client = BodhanClient(settings, session=session)

    if max_output_tokens == "omitted":
        client.translate("Hello", "hi")
    else:
        client.translate("Hello", "hi", max_output_tokens=max_output_tokens)

    assert (
        session.calls[0]["json"]["max_output_tokens"]
        == DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS
    )


def test_translate_below_context_limit_makes_one_call(settings):
    session = MockSession([
        response(payload={
            "translation": "अनुवाद",
            "truncated": False,
            "usage": {"completion_tokens": 2},
        })
    ])
    client = BodhanClient(settings, session=session)

    result = client.translate("Short text", "hi")

    assert result["translation"] == "अनुवाद"
    assert result["chunk_count"] == 1
    assert len(session.calls) == 1
    assert session.calls[0]["json"]["max_output_tokens"] == DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS


def test_translate_above_context_limit_chunks_and_orders_output(settings):
    source = " ".join(["word"] * 900)
    session = MockSession([
        response(payload={
            "translation": "first",
            "truncated": False,
            "usage": {"completion_tokens": 10},
        }),
        response(payload={
            "translation": "second",
            "truncated": False,
            "usage": {"completion_tokens": 10},
        }),
    ])
    client = BodhanClient(settings, session=session)

    result = client.translate(source, "ta", script="roman")

    assert result["translation"] == "first second"
    assert result["chunk_count"] == 2
    assert [call["json"]["text"] for call in session.calls] != [source]
    assert all(call["json"]["target_language"] == "ta" for call in session.calls)
    assert all(call["json"]["script"] == "roman" for call in session.calls)
    assert len(session.calls) == 2


def test_translate_explicit_limit_is_used_for_every_chunk(settings):
    source = " ".join(["word"] * 900)
    session = MockSession([
        response(payload={"translation": "first", "truncated": False, "usage": {}}),
        response(payload={"translation": "second", "truncated": False, "usage": {}}),
    ])
    client = BodhanClient(settings, session=session)

    result = client.translate(source, "hi", max_output_tokens=321)

    assert result["chunk_count"] == 2
    assert all(call["json"]["max_output_tokens"] == 321 for call in session.calls)


def test_translate_failed_middle_chunk_reports_partial_completion(settings):
    source = " ".join(["word"] * 900)
    session = MockSession([
        response(payload={"translation": "पहला", "truncated": False, "usage": {"completion_tokens": 5}}),
        response(status=503, payload={
            "error": {"message": "temporary", "code": "unavailable"}
        }),
    ])
    client = BodhanClient(settings, session=session, max_retries=0)

    with pytest.raises(TranslationChunkError) as caught:
        client.translate(source, "hi")

    assert caught.value.chunk_index == 2
    assert caught.value.chunk_count == 2
    assert caught.value.partial_translation == "पहला"


def test_translate_truncated_chunk_reports_partial_completion(settings):
    source = " ".join(["word"] * 900)
    session = MockSession([
        response(payload={
            "translation": "पहला",
            "truncated": True,
            "usage": {"completion_tokens": 5},
        })
    ])
    client = BodhanClient(settings, session=session)

    with pytest.raises(TranslationTruncatedError) as caught:
        client.translate(source, "hi")

    assert caught.value.partial is True
    assert caught.value.chunk_index == 1
    assert caught.value.chunk_count == 2


def test_translate_truncation_raises_typed_exception(settings):
    result_payload = {
        "translation": "अधूरी",
        "truncated": True,
        "usage": {"prompt_tokens": 3, "completion_tokens": 10, "total_tokens": 13},
    }
    session = MockSession([response(payload=result_payload)])
    client = BodhanClient(settings, session=session)

    with pytest.raises(TranslationTruncatedError) as caught:
        client.translate("Hello", "hi")

    assert caught.value.translation == ""
    assert caught.value.partial_translation == "अधूरी"
    assert caught.value.usage["total_tokens"] == 13


@pytest.mark.parametrize(
    "payload",
    [
        {"translation": "अनुवाद"},
        {"translation": "अनुवाद", "truncated": None},
        {"translation": "अनुवाद", "truncated": "false"},
        {"translation": "अनुवाद", "truncated": 0},
        {"translation": "अनुवाद", "truncated": []},
    ],
)
def test_translate_rejects_missing_or_invalid_truncation_metadata(settings, payload):
    session = MockSession([response(payload=payload)])
    client = BodhanClient(settings, session=session)

    with pytest.raises(BodhanResponseError, match="valid boolean 'truncated'"):
        client.translate("Hello", "hi")


@pytest.mark.parametrize("token_limit", [0, -1, -100])
def test_translate_rejects_non_positive_output_token_limit(settings, token_limit):
    client = BodhanClient(settings, session=MockSession([]))

    with pytest.raises(ValueError, match="greater than zero"):
        client.translate("Hello", "hi", max_output_tokens=token_limit)


def test_speak_endpoint_payload_key_and_raw_wav(settings):
    wav = b"RIFF\x24\x00\x00\x00WAVE"
    session = MockSession([
        response(content=wav, headers={"Content-Type": "audio/wav"})
    ])
    client = BodhanClient(settings, session=session)

    result = client.speak("नमस्ते", "Kavya", "hi", style="news")
    call = session.calls[0]

    assert call["method"] == "POST"
    assert call["url"] == "https://api.bodhan.ai/v1/audio/speech"
    assert call["headers"]["Authorization"] == "Bearer speak-secret"
    assert call["json"]["model"] == "indic-speak"
    assert call["json"]["input"] == "नमस्ते"
    assert call["json"]["voice"] == "Kavya"
    assert json.loads(call["json"]["instructions"]) == {"lang": "hi", "style": "news"}
    assert result == wav


def test_speak_rejects_empty_response(settings):
    session = MockSession([
        response(content=b"", headers={"Content-Type": "audio/wav"})
    ])
    client = BodhanClient(settings, session=session)

    with pytest.raises(BodhanResponseError, match="contained no audio"):
        client.speak("नमस्ते", "Kavya", "hi")


def test_speak_rejects_unexpected_content_type(settings):
    session = MockSession([
        response(
            content=b'{"unexpected": "json"}',
            headers={"Content-Type": "application/json"},
        )
    ])
    client = BodhanClient(settings, session=session)

    with pytest.raises(BodhanResponseError, match="Unexpected Bodhan speech Content-Type"):
        client.speak("नमस्ते", "Kavya", "hi")


def test_ocr_endpoint_payload_key_and_data_uri(tmp_path, settings):
    image = tmp_path / "page.png"
    image.write_bytes(b"\x89PNG-test")
    blocks = [{"order": 0, "label": "Paragraph", "text": "पाठ"}]

    session = MockSession([
        response(payload={
            "choices": [{"message": {"role": "assistant", "content": "पाठ"}}],
            "blocks": blocks,
        })
    ])
    client = BodhanClient(settings, session=session)

    result = client.ocr(image, table_format="markdown", max_tokens=4096)
    call = session.calls[0]

    assert call["method"] == "POST"
    assert call["url"] == "https://api.bodhan.ai/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer ocr-secret"
    assert call["json"]["model"] == "indic-ocr"
    assert call["json"]["table_format"] == "markdown"
    assert call["json"]["max_tokens"] == 4096

    data_uri = call["json"]["messages"][0]["content"][0]["image_url"]["url"]
    assert data_uri == (
        "data:image/png;base64,"
        + base64.b64encode(b"\x89PNG-test").decode("ascii")
    )
    assert result == {
    "content": "पाठ",
    "blocks": blocks,
    "usage": {},
}

def test_structured_api_error(settings):
    session = MockSession([
        response(
            status=400,
            payload={
                "error": {
                    "message": "Unsupported language",
                    "code": "unsupported_language",
                    "request_id": "req-123",
                }
            },
        )
    ])
    client = BodhanClient(settings, session=session)

    with pytest.raises(BodhanAPIError) as caught:
        client.translate("Hello", "bho")

    assert caught.value.status_code == 400
    assert caught.value.code == "unsupported_language"
    assert caught.value.request_id == "req-123"
    assert "Unsupported language" in str(caught.value)


def test_429_retry_is_bounded_and_respects_retry_after(settings):
    session = MockSession([
        response(
            status=429,
            payload={"error": {"message": "rate limited", "code": "rate_limit_exceeded_error"}},
            headers={"Retry-After": "0"},
        ),
        response(payload={"translation": "अनुवाद", "truncated": False}),
    ])
    delays = []
    client = BodhanClient(settings, session=session, max_retries=1, sleep=delays.append)

    assert client.translate("Hello", "hi")["translation"] == "अनुवाद"
    assert len(delays) == 1
    assert delays == [0.0]


def test_large_numeric_retry_after_is_capped(settings):
    session = MockSession([
        response(
            status=429,
            payload={"error": {"message": "rate limited", "code": "rate_limit_exceeded_error"}},
            headers={"Retry-After": "3600"},
        ),
        response(payload={"translation": "अनुवाद", "truncated": False}),
    ])
    delays = []
    client = BodhanClient(
        settings,
        session=session,
        max_retries=1,
        max_wait=30.0,
        sleep=delays.append,
    )

    assert client.translate("Hello", "hi")["translation"] == "अनुवाद"
    assert delays == [30.0]


def test_http_date_retry_after_is_capped(settings):
    future = datetime.now(timezone.utc) + timedelta(seconds=3600)
    retry_date = email.utils.format_datetime(future, usegmt=True)

    session = MockSession([
        response(
            status=429,
            payload={"error": {"message": "rate limited", "code": "rate_limit_exceeded_error"}},
            headers={"Retry-After": retry_date},
        ),
        response(payload={"translation": "अनुवाद", "truncated": False}),
    ])
    delays = []
    client = BodhanClient(
        settings,
        session=session,
        max_retries=1,
        max_wait=30.0,
        sleep=delays.append,
    )

    assert client.translate("Hello", "hi")["translation"] == "अनुवाद"
    assert len(delays) == 1
    assert 29.0 <= delays[0] <= 30.0


def test_permanent_4xx_is_not_retried(settings):
    session = MockSession([
        response(
            status=401,
            payload={"error": {"message": "bad key", "code": "invalid_api_key_error"}},
        )
    ])
    client = BodhanClient(settings, session=session, max_retries=3)

    with pytest.raises(BodhanAPIError):
        client.transcribe(__file__)

    assert len(session.calls) == 1


def test_network_retries_are_bounded(settings):
    session = MockSession([
        requests.ConnectionError("first"),
        requests.ConnectionError("second"),
        requests.ConnectionError("third"),
    ])
    delays = []
    client = BodhanClient(
        settings,
        session=session,
        max_retries=2,
        backoff_factor=0.25,
        sleep=delays.append,
    )

    with pytest.raises(BodhanNetworkError):
        client.translate("Hello", "hi")

    assert len(delays) == 2
    assert delays == [0.25, 0.5]


def test_transient_http_retry_succeeds(settings):
    session = MockSession([
        response(status=503, payload={"error": {"message": "temporary"}}),
        response(payload={"translation": "अनुवाद", "truncated": False, "usage": {"completion_tokens": 2}}),
    ])
    delays = []
    client = BodhanClient(settings, session=session, max_retries=1, sleep=delays.append)

    result = client.translate("Hello", "hi")

    assert result["translation"] == "अनुवाद"
    assert len(session.calls) == 2
    assert len(delays) == 1
    assert client.last_retry_stats["retry_attempts"] == 1
    assert client.last_retry_stats["retry_succeeded"] is True


def test_permanent_error_is_not_retried(settings):
    session = MockSession([
        response(status=401, payload={"error": {"message": "bad key", "code": "invalid_api_key"}}),
    ])
    client = BodhanClient(settings, session=session, max_retries=3)

    with pytest.raises(BodhanAPIError):
        client.translate("Hello", "hi")

    assert len(session.calls) == 1
    assert client.last_retry_stats["failure_category"] == "permanent"


def test_deadline_expires_before_retry(settings):
    session = MockSession([
        response(status=503, payload={"error": {"message": "temporary"}}),
    ])
    now = time.time()
    client = BodhanClient(
        settings,
        session=session,
        max_retries=2,
        backoff_factor=1.0,
        max_wait=30.0,
        deadline_epoch=now + 0.001,
    )

    with pytest.raises(BodhanDeadlineExceeded):
        client.translate("Hello", "hi")


def test_paid_timeout_is_uncertain_and_not_retried(settings):
    session = MockSession([requests.Timeout("timed out")])
    client = BodhanClient(settings, session=session, max_retries=3)

    with pytest.raises(BodhanUncertainOutcomeError):
        client.translate("Hello", "hi")

    assert len(session.calls) == 1
    assert client.last_retry_stats["uncertain_paid_outcome"] is True


def test_text_chunking_prefers_paragraph_and_sentence_boundaries():
    paragraph_text = ("पहला वाक्य।\n\n" + "बी" * 30) * 20
    chunks = split_text(paragraph_text, max_characters=150)

    assert len(chunks) > 1
    assert any("\n\n" not in chunk.text for chunk in chunks)
    assert all(chunk.end > chunk.start for chunk in chunks)


def test_text_chunking_does_not_split_indic_graphemes():
    indic = "क्ष" * 30 + "हिन्दी" * 100
    chunks = split_text(indic, max_characters=17)

    assert "".join(chunk.text for chunk in chunks) == indic
    for chunk in chunks:
        assert not chunk.text.endswith("\u094d")


def test_text_chunking_preserves_whitespace_and_exact_reconstruction():
    source = "  नमस्ते,\n\n  दूसरा वाक्य!  \r\n"
    chunks = split_text(source, max_characters=4000)

    assert "".join(chunk.text for chunk in chunks) == source
    assert chunks[0].start == 0
    assert chunks[-1].end == len(source)
    assert chunks[0].text == source


def test_text_chunking_handles_combining_marks_and_emoji():
    combining = "a\u0301e\u0301न\u093f"
    emoji = "👩🏽‍💻👨‍👩‍👧‍👦"
    source = combining + emoji

    chunks = split_text(source, max_characters=4000)
    assert "".join(chunk.text for chunk in chunks) == source


def test_text_chunking_rejects_grapheme_larger_than_limit():
    cluster = "👩🏽‍💻"

    with pytest.raises(TextChunkingError, match="grapheme cluster"):
        split_text(cluster, max_characters=2)
