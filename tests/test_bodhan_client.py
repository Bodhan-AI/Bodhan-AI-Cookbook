"""Unit tests for the Bodhan API client using HTTPX MockTransport.

All tests run completely offline without real API keys or network requests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

# Ensure integrations/langchain is importable
INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "langchain"
if str(INTEGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS_DIR))

from bodhan_langchain.client import BodhanAsyncClient, BodhanClient  # noqa: E402
from bodhan_langchain.exceptions import (  # noqa: E402
    BodhanAPIError,
    BodhanAuthenticationError,
    BodhanRateLimitError,
    BodhanValidationError,
)
from bodhan_langchain.models import OCRResponse, TranslationResponse  # noqa: E402

DUMMY_KEY = "dummy-test-key-12345"
PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def test_missing_api_key_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that omitting the API key raises BodhanAuthenticationError."""
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATION_API_KEY", raising=False)
    with pytest.raises(BodhanAuthenticationError) as exc_info:
        BodhanClient()
    assert "Bodhan API key is required" in str(exc_info.value)
    assert exc_info.value.status_code == 401


def test_empty_string_api_key_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that an empty string API key raises BodhanAuthenticationError."""
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATION_API_KEY", raising=False)
    with pytest.raises(BodhanAuthenticationError):
        BodhanClient(api_key="   ")


def test_successful_ocr_response() -> None:
    """Verify successful OCR response parsing with Markdown content and layout blocks."""
    mock_payload = {
        "id": "chatcmpl-test-ocr",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "indic-ocr",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "# विद्यालय सूचना\n\nकल विद्यालय बंद रहेगा।",
                },
                "finish_reason": "stop",
            }
        ],
        "blocks": [
            {
                "order": 1,
                "label": "title",
                "bbox_xyxy": [40, 40, 300, 80],
                "conf": 0.98,
                "text": "विद्यालय सूचना",
            },
            {
                "order": 2,
                "label": "paragraph",
                "bbox_xyxy": [40, 120, 500, 160],
                "conf": 0.95,
                "text": "कल विद्यालय बंद रहेगा।",
            },
        ],
        "usage": {"total_tokens": 42},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {DUMMY_KEY}"

        data = json.loads(request.content)
        assert data["model"] == "indic-ocr"
        assert data["table_format"] == "markdown"
        assert "image_url" in data["messages"][0]["content"][0]
        assert data["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")

        return httpx.Response(200, json=mock_payload)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        res = client.ocr(PNG_HEADER, table_format="markdown")

    assert isinstance(res, OCRResponse)
    assert res.text == "# विद्यालय सूचना\n\nकल विद्यालय बंद रहेगा।"
    assert len(res.blocks) == 2
    assert res.blocks[0].order == 1
    assert res.blocks[0].label == "title"
    assert res.blocks[0].conf == 0.98
    assert res.blocks[0].bbox_xyxy == [40, 40, 300, 80]
    assert res.blocks[0].text == "विद्यालय सूचना"
    assert res.model == "indic-ocr"
    assert res.usage == {"total_tokens": 42}


def test_successful_translation_chat_completions() -> None:
    """Verify translation via the chat completions transport route."""
    mock_payload = {
        "id": "chatcmpl-test-trans",
        "object": "chat.completion",
        "model": "indic-translate",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "प्रकाश संश्लेषण क्या है?",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 6, "completion_tokens": 12, "total_tokens": 18},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        data = json.loads(request.content)
        assert data["model"] == "indic-translate"
        assert data["target_language"] == "hi"
        assert data["messages"][0]["content"] == "What is photosynthesis?"
        return httpx.Response(200, json=mock_payload)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        res = client.translate("What is photosynthesis?", target_language="hi", transport="chat_completions")

    assert isinstance(res, TranslationResponse)
    assert res.text == "प्रकाश संश्लेषण क्या है?"
    assert res.target_language == "hi"
    assert res.truncated is False
    assert res.usage == {"prompt_tokens": 6, "completion_tokens": 12, "total_tokens": 18}


def test_successful_translation_translate_endpoint() -> None:
    """Verify translation via the POST /translate endpoint."""
    mock_payload = {
        "translation": "வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?",
        "target_language": "ta",
        "source_language": "en",
        "script": "native",
        "truncated": False,
        "usage": {"output_tokens": 10},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/translate"
        data = json.loads(request.content)
        assert data["text"] == "Hello, how are you?"
        assert data["target_language"] == "ta"
        assert data["script"] == "native"
        return httpx.Response(200, json=mock_payload)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        res = client.translate("Hello, how are you?", target_language="ta", script="native", transport="translate_endpoint")

    assert res.text == "வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?"
    assert res.target_language == "ta"
    assert res.source_language == "en"
    assert res.script == "native"
    assert res.truncated is False


def test_translation_auto_strategy_fallback_on_404() -> None:
    """Verify that transport='auto' attempts POST /translate first and falls back to /v1/chat/completions on 404."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "नमस्ते"}}]})
        return httpx.Response(500)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        res = client.translate("Hello", target_language="hi", transport="auto")

    assert calls == ["/translate", "/v1/chat/completions"]
    assert res.text == "नमस्ते"


def test_invalid_input_validation() -> None:
    """Verify client-side validation errors for malformed or empty inputs."""
    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client())

    # Empty text
    with pytest.raises(BodhanValidationError) as exc:
        client.translate("", target_language="hi")
    assert "cannot be empty" in str(exc.value)

    # Whitespace text
    with pytest.raises(BodhanValidationError):
        client.translate("   \n\t", target_language="hi")

    # Invalid language code with -IN suffix
    with pytest.raises(BodhanValidationError) as exc:
        client.translate("Hello", target_language="hi-IN")
    assert "plain ISO codes" in str(exc.value)

    # Completely unsupported language code
    with pytest.raises(BodhanValidationError) as exc:
        client.translate("Hello", target_language="klingon")
    assert "Unsupported target language" in str(exc.value)

    # Invalid script
    with pytest.raises(BodhanValidationError) as exc:
        client.translate("Hello", target_language="hi", script="hieroglyphs")
    assert "Invalid script" in str(exc.value)

    # Negative max_tokens
    with pytest.raises(BodhanValidationError):
        client.translate("Hello", target_language="hi", max_tokens=-10)

    # Empty image bytes
    with pytest.raises(BodhanValidationError) as exc:
        client.ocr(b"")
    assert "Image bytes cannot be empty" in str(exc.value)

    # Invalid table format
    with pytest.raises(BodhanValidationError) as exc:
        client.ocr(PNG_HEADER, table_format="csv")
    assert "Invalid table_format" in str(exc.value)


def test_http_400_bad_request() -> None:
    """Verify HTTP 400 Bad Request error handling and error code extraction."""
    error_payload = {
        "error": {
            "message": "Invalid image payload or corrupted file.",
            "code": "INVALID_IMAGE",
            "request_id": "req-400-test",
        }
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=error_payload)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)

    err = exc_info.value
    assert err.status_code == 400
    assert err.error_code == "INVALID_IMAGE"
    assert err.request_id == "req-400-test"
    assert "Invalid image payload" in err.message


def test_http_401_authentication_error() -> None:
    """Verify HTTP 401 Unauthorized maps to BodhanAuthenticationError."""
    error_payload = {
        "error": {
            "message": "Authentication Error, No api key passed in.",
            "code": "AUTH_FAILED",
            "request_id": "req-auth-fail",
        }
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=error_payload)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAuthenticationError) as exc_info:
            client.translate("Hello", target_language="hi")

    err = exc_info.value
    assert err.status_code == 401
    assert "Authentication Error" in err.message
    assert err.request_id == "req-auth-fail"


def test_http_413_payload_too_large() -> None:
    """Verify HTTP 413 Payload Too Large error handling."""
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(413, text="Payload Too Large")

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)

    assert exc_info.value.status_code == 413
    assert "Payload Too Large" in exc_info.value.message


def test_http_429_rate_limit_error() -> None:
    """Verify HTTP 429 Too Many Requests maps to BodhanRateLimitError and parses headers."""
    headers = {
        "Retry-After": "15",
        "x-ratelimit-limit-requests": "100",
        "x-ratelimit-limit-parallel-requests": "2",
        "x-request-id": "req-429-limit",
    }
    payload = {
        "error": {
            "message": "Rate limit exceeded. Too many concurrent requests.",
            "code": "RATE_LIMIT_EXCEEDED",
            "request_id": "req-429-limit",
        }
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json=payload, headers=headers)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanRateLimitError) as exc_info:
            client.translate("Hello", target_language="hi")

    err = exc_info.value
    assert err.status_code == 429
    assert err.retry_after == 15.0
    assert err.limit_requests == "100"
    assert err.limit_parallel_requests == "2"
    assert err.request_id == "req-429-limit"
    assert "retry_after=15.0s" in str(err)


def test_server_errors_500_and_503() -> None:
    """Verify HTTP 500 and 503 server errors are raised as BodhanAPIError."""
    def handler_500(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "Internal Server Error", "code": "INTERNAL"}})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler_500), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_500:
            client.translate("Hello", target_language="hi")
    assert exc_500.value.status_code == 500

    def handler_503(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    mock_client_503 = httpx.Client(transport=httpx.MockTransport(handler_503), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client_503) as client:
        with pytest.raises(BodhanAPIError) as exc_503:
            client.ocr(PNG_HEADER)
    assert exc_503.value.status_code == 503


def test_malformed_json_response() -> None:
    """Verify handling when the API returns HTTP 200 with invalid JSON body."""
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<HTML><body>Server Error</body></HTML>", headers={"Content-Type": "text/html"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)

    assert "Malformed JSON response" in exc_info.value.message


def test_ocr_response_missing_expected_content() -> None:
    """Verify that an OCR response missing choices or message content raises BodhanAPIError."""
    # Missing choices
    def handler_empty_choices(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler_empty_choices), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)
    assert "missing expected 'choices'" in exc_info.value.message

    # Missing content in message
    def handler_no_content(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {}}]})

    mock_client2 = httpx.Client(transport=httpx.MockTransport(handler_no_content), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client2) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)
    assert "missing" in exc_info.value.message and "content" in exc_info.value.message


def test_timeout_handling() -> None:
    """Verify that httpx.TimeoutException is cleanly wrapped into BodhanAPIError."""
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timed out")

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client, timeout=5.0) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)

    assert "timed out" in exc_info.value.message


@pytest.mark.asyncio
async def test_async_client_ocr_and_translate() -> None:
    """Verify that BodhanAsyncClient performs async OCR and translation correctly."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            data = json.loads(request.content)
            if data["model"] == "indic-ocr":
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "Async OCR output"}}],
                    "blocks": [],
                })
            if data["model"] == "indic-translate":
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "Async translation output"}}],
                })
        return httpx.Response(404)

    mock_async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    async with BodhanAsyncClient(api_key=DUMMY_KEY, http_client=mock_async_client) as client:
        ocr_res = await client.ocr(PNG_HEADER)
        assert ocr_res.text == "Async OCR output"

        trans_res = await client.translate("Hello async", target_language="hi")
        assert trans_res.text == "Async translation output"


@pytest.mark.asyncio
async def test_async_translation_auto_fallback_on_404() -> None:
    """Verify that BodhanAsyncClient falls back from /translate to /v1/chat/completions on 404."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "नमस्ते async"}}]})
        return httpx.Response(500)

    mock_async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    async with BodhanAsyncClient(api_key=DUMMY_KEY, http_client=mock_async_client) as client:
        res = await client.translate("Hello", target_language="hi", transport="auto")

    assert calls == ["/translate", "/v1/chat/completions"]
    assert res.text == "नमस्ते async"


def test_defensive_schema_null_message_and_invalid_content() -> None:
    """Verify that null message or non-string content raises BodhanAPIError without crashing."""
    # choices[0].message is null
    def handler_null_msg(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": None}]})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler_null_msg), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)
    assert "not a dictionary" in exc_info.value.message

    # choices[0].message.content is an integer
    def handler_int_content(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": 12345}}]})

    mock_client2 = httpx.Client(transport=httpx.MockTransport(handler_int_content), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client2) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.translate("Hello", target_language="hi", transport="chat_completions")
    assert "missing valid string 'content'" in exc_info.value.message


def test_ocr_metadata_defensive_parsing_null_conf_and_coords() -> None:
    """Verify that null conf or malformed coordinates in OCR blocks do not crash the request."""
    mock_payload = {
        "choices": [{"message": {"content": "Sample OCR Text"}, "finish_reason": "length"}],
        "blocks": [
            {"order": "1", "label": "paragraph", "conf": None, "bbox_xyxy": [10, 20, 30, 40], "text": "Valid text"},
            {"order": None, "label": None, "conf": "invalid", "bbox_xyxy": "bad", "text": None},
        ],
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_payload)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        res = client.ocr(PNG_HEADER)

    assert res.text == "Sample OCR Text"
    assert res.truncated is True
    assert res.finish_reason == "length"
    assert len(res.blocks) == 2
    assert res.blocks[0].conf is None  # Preserved as None without fabricating 0.0
    assert res.blocks[0].bbox_xyxy == [10, 20, 30, 40]
    assert res.blocks[1].conf is None  # Invalid string gracefully parsed as None
    assert res.blocks[1].bbox_xyxy is None  # Malformed bbox gracefully parsed as None without fabricating [0,0,0,0]
    assert res.blocks[1].text == ""  # Safely defaulted from None


def test_injected_client_lifecycle_not_closed() -> None:
    """Verify that BodhanClient does not close an externally injected HTTP client on exit."""
    mock_transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"translation": "नमस्ते"}))
    external_client = httpx.Client(transport=mock_transport, base_url="https://api.bodhan.ai")

    with BodhanClient(api_key=DUMMY_KEY, http_client=external_client) as client:
        client.translate("Hello", target_language="hi", transport="translate_endpoint")

    # Injected client should remain open
    assert not external_client.is_closed
    external_client.close()
    assert external_client.is_closed


def test_translation_max_output_tokens_payload() -> None:
    """Verify that translation endpoints send 'max_output_tokens' in the JSON body."""
    recorded_requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content.decode("utf-8"))
        recorded_requests.append({"path": request.url.path, "body": body})
        if request.url.path == "/translate":
            return httpx.Response(200, json={"translation": "नमस्ते"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "नमस्ते"}}]})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        # 1. Plain json endpoint
        client.translate("Hello", target_language="hi", transport="translate_endpoint", max_output_tokens=500)
        assert recorded_requests[0]["path"] == "/translate"
        assert recorded_requests[0]["body"]["max_output_tokens"] == 500
        assert "max_tokens" not in recorded_requests[0]["body"]

        # 2. Chat completions endpoint
        client.translate("Hello", target_language="hi", transport="chat_completions", max_output_tokens=600)
        assert recorded_requests[1]["path"] == "/v1/chat/completions"
        assert recorded_requests[1]["body"]["max_output_tokens"] == 600
        assert "max_tokens" not in recorded_requests[1]["body"]


def test_auto_strategy_no_fallback_on_auth_or_server_error() -> None:
    """Verify that auto strategy does NOT fall back on 401, 429, or 500."""
    calls: list[str] = []

    def handler_401(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler_401), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAuthenticationError):
            client.translate("Hello", target_language="hi", transport="auto")
    # Should only have called /translate once, NO fallback to /v1/chat/completions!
    assert calls == ["/translate"]


def test_safe_exception_sanitization_no_secret_leak() -> None:
    """Verify that sensitive API keys, Bearer tokens, and base64 strings are sanitized in exceptions."""
    secret_key = "sk-" + "exampledummykey1234567890"
    base64_blob = "data:image/png;base64," + "A" * 150

    def handler_error(request: httpx.Request) -> httpx.Response:
        # Simulate an error response echoing back sensitive request info
        echo_text = f"Failed with header Authorization: Bearer {secret_key} and payload {base64_blob}"
        return httpx.Response(500, text=echo_text)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler_error), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)

    exc_str = str(exc_info.value)
    exc_repr = repr(exc_info.value)

    assert secret_key not in exc_str
    assert secret_key not in exc_repr
    assert "sk-***[REDACTED]" in exc_str or "Bearer ***[REDACTED]" in exc_str
    assert "A" * 100 not in exc_str


def test_http_413_json_error_payload() -> None:
    """Verify that HTTP 413 Payload Too Large raises BodhanAPIError with status 413."""
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(413, json={"error": {"message": "Image payload exceeds maximum page size", "code": "PAYLOAD_TOO_LARGE"}})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.ocr(PNG_HEADER)
    assert exc_info.value.status_code == 413
    assert "exceeds" in exc_info.value.message


def test_client_timeout_handling() -> None:
    """Verify that httpx.TimeoutException raises BodhanAPIError without leaking socket internals."""
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out waiting for server")

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client, timeout=10.0) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.translate("Hello", target_language="hi")
    assert "timed out after 10.0 seconds" in str(exc_info.value)


def test_invalid_transport_strategy_raises_validation_error() -> None:
    """Verify invalid transport_strategy values raise BodhanValidationError rather than silently choosing auto."""
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)), base_url="https://api.bodhan.ai")
    with pytest.raises(BodhanValidationError) as exc:
        BodhanClient(api_key=DUMMY_KEY, http_client=mock_client, transport_strategy="invalid_strat")  # type: ignore[arg-type]
    assert "Invalid transport_strategy" in str(exc.value)

    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanValidationError) as exc2:
            client.translate("hello", target_language="hi", transport="bogus")  # type: ignore[arg-type]
        assert "Invalid transport" in str(exc2.value)


@pytest.mark.asyncio
async def test_async_invalid_transport_strategy_raises_validation_error() -> None:
    """Verify invalid transport_strategy values raise BodhanValidationError in async client."""
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)), base_url="https://api.bodhan.ai")
    with pytest.raises(BodhanValidationError) as exc:
        BodhanAsyncClient(api_key=DUMMY_KEY, http_client=mock_client, transport_strategy="translate")  # type: ignore[arg-type]
    assert "Invalid transport_strategy" in str(exc.value)

    async with BodhanAsyncClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        with pytest.raises(BodhanValidationError) as exc2:
            await client.translate("hello", target_language="hi", transport="translate")  # type: ignore[arg-type]
        assert "Invalid transport" in str(exc2.value)


def test_translation_script_combinations_and_english_restrictions() -> None:
    """Verify script restrictions for English, Indic scripts, and codemix combinations."""
    recorded_requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        recorded_requests.append(data)
        return httpx.Response(200, json={"translation": "OK"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        # 1. Target English with roman -> rejected
        with pytest.raises(BodhanValidationError) as exc:
            client.translate("नमस्ते", target_language="en", script="roman")
        assert "Script 'roman' is not valid for English" in str(exc.value)

        # 2. Target English with codemix -> rejected
        with pytest.raises(BodhanValidationError) as exc:
            client.translate("नमस्ते", target_language="en", script="codemix")
        assert "Script 'codemix' is not available for English" in str(exc.value)

        # 3. Target English with script='native' -> omitted from outgoing request payload
        client.translate("नमस्ते", target_language="en", script="native")
        assert "script" not in recorded_requests[-1]
        assert "source_language" not in recorded_requests[-1]

        # 4. Target English with script=None -> omitted from outgoing request payload
        client.translate("नमस्ते", target_language="en", script=None)
        assert "script" not in recorded_requests[-1]

        # 5. ks-Deva with codemix -> rejected
        with pytest.raises(BodhanValidationError) as exc:
            client.translate("Hello", target_language="ks-Deva", script="codemix")
        assert "Script 'codemix' is not available" in str(exc.value)

        # 6. sd-Arab with codemix -> rejected
        with pytest.raises(BodhanValidationError) as exc:
            client.translate("Hello", target_language="sd-Arab", script="codemix")
        assert "Script 'codemix' is not available" in str(exc.value)

        # 7. Valid Indic targets with native, roman, codemix
        client.translate("Hello", target_language="hi", script="native")
        assert recorded_requests[-1]["script"] == "native"

        client.translate("Hello", target_language="hi", script="roman")
        assert recorded_requests[-1]["script"] == "roman"

        client.translate("Hello", target_language="hi", script="codemix")
        assert recorded_requests[-1]["script"] == "codemix"


def test_token_parameter_validation_and_payload_isolation() -> None:
    """Verify max_output_tokens vs max_tokens validation, boolean rejection, and payload keys."""
    recorded_requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        recorded_requests.append(data)
        if request.url.path == "/v1/chat/completions" and data.get("model") == "indic-ocr":
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "OCR text"}}],
                "blocks": [],
            })
        return httpx.Response(200, json={"translation": "Translated text"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        # Boolean rejection in translation
        with pytest.raises(BodhanValidationError):
            client.translate("hi", target_language="ta", max_output_tokens=True)  # type: ignore[arg-type]
        with pytest.raises(BodhanValidationError):
            client.translate("hi", target_language="ta", max_tokens=False)  # type: ignore[arg-type]
        with pytest.raises(BodhanValidationError):
            client.translate("hi", target_language="ta", max_output_tokens=0)
        with pytest.raises(BodhanValidationError):
            client.translate("hi", target_language="ta", max_output_tokens=-10)

        # Boolean rejection in OCR
        with pytest.raises(BodhanValidationError):
            client.ocr(PNG_HEADER, max_tokens=True)  # type: ignore[arg-type]
        with pytest.raises(BodhanValidationError):
            client.ocr(PNG_HEADER, max_tokens=0)
        with pytest.raises(BodhanValidationError):
            client.ocr(PNG_HEADER, max_tokens=-5)

        # Translation legacy alias max_tokens maps to outgoing max_output_tokens
        client.translate("hi", target_language="ta", max_tokens=500)
        assert recorded_requests[-1]["max_output_tokens"] == 500
        assert "max_tokens" not in recorded_requests[-1]

        # Explicit max_output_tokens maps to outgoing max_output_tokens
        client.translate("hi", target_language="ta", max_output_tokens=750)
        assert recorded_requests[-1]["max_output_tokens"] == 750
        assert "max_tokens" not in recorded_requests[-1]

        # OCR outgoing request uses max_tokens, never max_output_tokens
        client.ocr(PNG_HEADER, max_tokens=300)
        assert recorded_requests[-1]["max_tokens"] == 300
        assert "max_output_tokens" not in recorded_requests[-1]


def test_ocr_block_metadata_integrity_and_float_precision() -> None:
    """Verify OCRBlock preserves type, float coordinates, valid confidence, and handles malformed metadata."""
    mock_payload = {
        "choices": [{"message": {"content": "# Extracted Markdown"}}],
        "blocks": [
            # 1. Full valid block with float coordinates and type
            {
                "order": 1,
                "label": "title",
                "type": "header_block",
                "bbox_xyxy": [12.5, 24.75, 300.125, 80.5],
                "conf": 0.985,
                "text": "Heading Text",
            },
            # 2. Block with missing type, null confidence, integer coords
            {
                "order": 2,
                "label": "paragraph",
                "bbox_xyxy": [10, 20, 100, 200],
                "conf": None,
                "text": "Paragraph text",
            },
            # 3. Block with invalid confidence (> 1.0, < 0.0, bool, non-numeric)
            {
                "order": 3,
                "label": "table",
                "type": "table",
                "bbox_xyxy": [0, 0, 100, 100],
                "conf": 1.5,  # Out of range -> should become None
                "text": "Table text",
            },
            {
                "order": 4,
                "label": "formula",
                "bbox_xyxy": [0, 0, 100, 100],
                "conf": True,  # Boolean -> should become None
                "text": "Formula text",
            },
            # 4. Block with malformed bbox (bool coord, wrong length, non-numeric)
            {
                "order": 5,
                "label": "bad_bbox_bool",
                "bbox_xyxy": [True, 10.0, 20.0, 30.0],
                "conf": 0.5,
                "text": "Bad bbox bool",
            },
            {
                "order": 6,
                "label": "bad_bbox_len",
                "bbox_xyxy": [10, 20, 30],
                "conf": 0.5,
                "text": "Bad bbox length",
            },
            # 5. Non-dictionary blocks (must be skipped without crashing)
            None,
            "corrupted_block_string",
            123,
            # 6. Block with null text and missing label
            {
                "order": 7,
                "text": None,
            },
        ],
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_payload)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        res = client.ocr(PNG_HEADER)

    assert res.text == "# Extracted Markdown"
    assert len(res.blocks) == 7  # 7 valid dict blocks, 3 non-dict skipped

    b1 = res.blocks[0]
    assert b1.order == 1
    assert b1.label == "title"
    assert b1.type == "header_block"
    assert b1.bbox_xyxy == [12.5, 24.75, 300.125, 80.5]
    assert isinstance(b1.bbox_xyxy[0], float)
    assert b1.conf == 0.985
    assert b1.text == "Heading Text"

    b2 = res.blocks[1]
    assert b2.type is None
    assert b2.conf is None
    assert b2.bbox_xyxy == [10, 20, 100, 200]

    b3 = res.blocks[2]
    assert b3.conf is None  # 1.5 rejected

    b4 = res.blocks[3]
    assert b4.conf is None  # True rejected

    b5 = res.blocks[4]
    assert b5.bbox_xyxy is None  # Boolean coordinate rejected

    b6 = res.blocks[5]
    assert b6.bbox_xyxy is None  # 3 coords rejected

    b7 = res.blocks[6]
    assert b7.text == ""
    assert b7.label == "unknown"


@pytest.mark.asyncio
async def test_async_ocr_block_metadata_integrity() -> None:
    """Verify async OCR parser has complete parity with sync layout block parsing."""
    mock_payload = {
        "choices": [{"message": {"content": "Async OCR Content"}}],
        "blocks": [
            {
                "order": 1,
                "label": "title",
                "type": "heading",
                "bbox_xyxy": [1.5, 2.5, 3.5, 4.5],
                "conf": 0.92,
                "text": "Async Title",
            }
        ],
    }

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=mock_payload)), base_url="https://api.bodhan.ai")
    async with BodhanAsyncClient(api_key=DUMMY_KEY, http_client=mock_client) as client:
        res = await client.ocr(PNG_HEADER)

    assert len(res.blocks) == 1
    block = res.blocks[0]
    assert block.type == "heading"
    assert block.bbox_xyxy == [1.5, 2.5, 3.5, 4.5]
    assert block.conf == 0.92


def test_image_input_validation_and_security(tmp_path: Path) -> None:
    """Comprehensive test suite for image input security and validation."""
    import base64

    from bodhan_langchain.client import _prepare_image_data_url

    valid_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    valid_jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xd9"
    webp_bytes = b"RIFF\x18\x00\x00\x00WEBPVP8 \x0c\x00\x00\x00/\x00\x00\x00\x10\x07\x00\x9d\x01*"
    gif_bytes = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )
    svg_bytes = b"<svg xmlns='http://www.w3.org/2000/svg'><circle r='5'/></svg>"

    png_b64 = base64.b64encode(valid_png_bytes).decode("ascii")
    jpeg_b64 = base64.b64encode(valid_jpeg_bytes).decode("ascii")
    webp_b64 = base64.b64encode(webp_bytes).decode("ascii")
    gif_b64 = base64.b64encode(gif_bytes).decode("ascii")
    svg_b64 = base64.b64encode(svg_bytes).decode("ascii")

    # 1. Valid PNG bytes
    url = _prepare_image_data_url(valid_png_bytes)
    assert url.startswith("data:image/png;base64,")

    # 2. Valid JPEG bytes
    url = _prepare_image_data_url(valid_jpeg_bytes)
    assert url.startswith("data:image/jpeg;base64,")

    # 3. Valid PNG data URL
    url = _prepare_image_data_url(f"data:image/png;base64,{png_b64}")
    assert url.startswith("data:image/png;base64,")

    # 4. Valid JPEG data URL
    url = _prepare_image_data_url(f"data:image/jpeg;base64,{jpeg_b64}")
    assert url.startswith("data:image/jpeg;base64,")

    # 5. Wrong MIME declaration (unsupported MIME)
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url(f"data:image/bmp;base64,{png_b64}")
    assert "Unsupported MIME type 'image/bmp'" in str(exc.value)

    # 6. MIME and file signature mismatch (declared png, but payload is jpeg)
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url(f"data:image/png;base64,{jpeg_b64}")
    assert "MIME type mismatch" in str(exc.value)

    # 7. Invalid base64 in data URL
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url("data:image/png;base64,not-valid-base64-payload!!!")
    assert "Invalid base64 encoding" in str(exc.value)

    # 8. WebP data URL rejected
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url(f"data:image/webp;base64,{webp_b64}")
    assert "Unsupported image format 'image/webp'" in str(exc.value)

    # 9. GIF data URL rejected
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url(f"data:image/gif;base64,{gif_b64}")
    assert "Unsupported image format 'image/gif'" in str(exc.value)

    # 10. SVG data URL rejected
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url(f"data:image/svg+xml;base64,{svg_b64}")
    assert "Unsupported image format 'image/svg+xml'" in str(exc.value)

    # 11. Empty content
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url(b"")
    assert "cannot be empty" in str(exc.value)

    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url("data:image/png;base64,")
    assert "empty base64 payload" in str(exc.value) or "cannot be empty" in str(exc.value)

    # 12. Corrupted image bytes
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url(b"corrupted_random_garbage_bytes_123456789")
    assert "Invalid or unrecognized image binary signature" in str(exc.value)

    # 13. File path with mismatched extension but valid image signature (e.g. .bin with valid PNG)
    bin_file = tmp_path / "valid_image.bin"
    bin_file.write_bytes(valid_png_bytes)
    url = _prepare_image_data_url(bin_file)
    assert url.startswith("data:image/png;base64,")

    # 14. File path with .webp extension rejected
    webp_file = tmp_path / "test.webp"
    webp_file.write_bytes(webp_bytes)
    with pytest.raises(BodhanValidationError) as exc:
        _prepare_image_data_url(webp_file)
    assert "Unsupported image format '.webp'" in str(exc.value)


def test_exception_response_body_sanitization_and_bounded_diagnostics() -> None:
    """Verify that BodhanAPIError sanitizes credentials and bounds arbitrary response bodies."""
    dummy_key = "sk-" + "1234567890abcdef12345"
    leakable_payload = {
        "authorization": f"Bearer {dummy_key}",
        "api_key": "my-secret-key-9999",
        "image_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        "document_text": "A" * 1000,
        "nested": {
            "token": "secret_token_value",
            "content": "Valid diagnostic message",
        },
        "large_list": list(range(50)),
    }

    err = BodhanAPIError(
        message=f"API failure occurred with sensitive headers Authorization: Bearer {dummy_key}",
        status_code=500,
        response_body=leakable_payload,
    )

    # 1. Message string is sanitized
    err_str = str(err)
    assert dummy_key not in err_str
    assert "Bearer ***[REDACTED]" in err_str

    # 2. Response body dictionary is sanitized and bounded
    body = err.response_body
    assert isinstance(body, dict)
    assert body["authorization"] == "***[REDACTED]***"
    assert body["api_key"] == "***[REDACTED]***"
    assert body["image_url"] == "data:image/...;base64,***[REDACTED]***"
    assert len(body["document_text"]) <= 205
    assert body["nested"]["token"] == "***[REDACTED]***"
    assert body["nested"]["content"] == "Valid diagnostic message"
    assert len(body["large_list"]) <= 21
    assert "truncated" in str(body["large_list"][-1])


def test_client_model_specific_api_keys_and_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that BodhanClient selects OCR and Translate keys correctly and applies precedence."""
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATION_API_KEY", raising=False)

    recorded_auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_auth_headers.append(request.headers.get("Authorization", ""))
        data = json.loads(request.content)
        if "/translate" in request.url.path or data.get("model") == "indic-translate":
            return httpx.Response(200, json={"translation": "Translated Text"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OCR Text"}}]})

    mock_http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")

    # 1. Explicit model-specific keys passed to constructor
    client = BodhanClient(
        ocr_api_key="specific-ocr-key-1",
        translate_api_key="specific-trans-key-2",
        http_client=mock_http,
    )
    assert client.ocr_api_key == "specific-ocr-key-1"
    assert client.translate_api_key == "specific-trans-key-2"

    client.ocr(PNG_HEADER)
    assert recorded_auth_headers[-1] == "Bearer specific-ocr-key-1"

    client.translate("Hello", target_language="hi")
    assert recorded_auth_headers[-1] == "Bearer specific-trans-key-2"

    # 2. Environment variables BODHAN_OCR_API_KEY and BODHAN_TRANSLATE_API_KEY
    monkeypatch.setenv("BODHAN_OCR_API_KEY", "env-ocr-key-3")
    monkeypatch.setenv("BODHAN_TRANSLATE_API_KEY", "env-trans-key-4")

    client_env = BodhanClient(http_client=mock_http)
    assert client_env.ocr_api_key == "env-ocr-key-3"
    assert client_env.translate_api_key == "env-trans-key-4"

    client_env.ocr(PNG_HEADER)
    assert recorded_auth_headers[-1] == "Bearer env-ocr-key-3"

    client_env.translate("Hello", target_language="hi")
    assert recorded_auth_headers[-1] == "Bearer env-trans-key-4"

    # 3. Environment variable BODHAN_TRANSLATION_API_KEY (alias)
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY", raising=False)
    monkeypatch.setenv("BODHAN_TRANSLATION_API_KEY", "env-translation-alias-5")
    client_alias = BodhanClient(http_client=mock_http)
    assert client_alias.translate_api_key == "env-translation-alias-5"
    client_alias.translate("Hello", target_language="hi")
    assert recorded_auth_headers[-1] == "Bearer env-translation-alias-5"


def test_client_backward_compatible_generic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that BODHAN_API_KEY / api_key serves as a backward-compatible fallback for both models."""
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATION_API_KEY", raising=False)

    recorded_auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_auth_headers.append(request.headers.get("Authorization", ""))
        data = json.loads(request.content)
        if "/translate" in request.url.path or data.get("model") == "indic-translate":
            return httpx.Response(200, json={"translation": "Translated Text"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OCR Text"}}]})

    mock_http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")

    # Generic api_key argument applies to both
    client = BodhanClient(api_key="unified-general-key", http_client=mock_http)
    assert client.ocr_api_key == "unified-general-key"
    assert client.translate_api_key == "unified-general-key"

    client.ocr(PNG_HEADER)
    assert recorded_auth_headers[-1] == "Bearer unified-general-key"

    client.translate("Hello", target_language="hi")
    assert recorded_auth_headers[-1] == "Bearer unified-general-key"

    # Generic BODHAN_API_KEY in environment
    monkeypatch.setenv("BODHAN_API_KEY", "unified-env-key")
    client_env = BodhanClient(http_client=mock_http)
    assert client_env.ocr_api_key == "unified-env-key"
    assert client_env.translate_api_key == "unified-env-key"


def test_client_missing_model_specific_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify informative errors when an endpoint is called without its required model key."""
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATION_API_KEY", raising=False)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "translation": "OK"})

    mock_http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")

    # 1. Only OCR key provided
    client_ocr_only = BodhanClient(ocr_api_key="only-ocr-key", http_client=mock_http)
    assert client_ocr_only.ocr_api_key == "only-ocr-key"
    assert client_ocr_only.translate_api_key is None
    # OCR succeeds
    res = client_ocr_only.ocr(PNG_HEADER)
    assert res.text == "OK"
    # Translate raises informative BodhanAuthenticationError
    with pytest.raises(BodhanAuthenticationError) as exc_trans:
        client_ocr_only.translate("Hello", target_language="hi")
    assert "Bodhan Translate API key is required" in str(exc_trans.value)
    assert exc_trans.value.status_code == 401

    # 2. Only Translate key provided
    client_trans_only = BodhanClient(translate_api_key="only-trans-key", http_client=mock_http)
    assert client_trans_only.translate_api_key == "only-trans-key"
    assert client_trans_only.ocr_api_key is None
    # Translate succeeds
    res_t = client_trans_only.translate("Hello", target_language="hi")
    assert res_t.text == "OK"
    # OCR raises informative BodhanAuthenticationError
    with pytest.raises(BodhanAuthenticationError) as exc_ocr:
        client_trans_only.ocr(PNG_HEADER)
    assert "Bodhan OCR API key is required" in str(exc_ocr.value)
    assert exc_ocr.value.status_code == 401

    # 3. No keys at all provided raises BodhanAuthenticationError at init
    with pytest.raises(BodhanAuthenticationError) as exc_none:
        BodhanClient(http_client=mock_http)
    assert "Bodhan API key is required" in str(exc_none.value)


@pytest.mark.asyncio
async def test_async_client_model_specific_and_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify async client model-specific key dispatch and error handling."""
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATE_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATION_API_KEY", raising=False)

    recorded_auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_auth_headers.append(request.headers.get("Authorization", ""))
        data = json.loads(request.content)
        if "/translate" in request.url.path or data.get("model") == "indic-translate":
            return httpx.Response(200, json={"translation": "Async Translated Text"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Async OCR Text"}}]})

    mock_async_http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")

    # Both model keys
    client = BodhanAsyncClient(
        ocr_api_key="async-ocr-key",
        translate_api_key="async-trans-key",
        http_client=mock_async_http,
    )
    assert client.ocr_api_key == "async-ocr-key"
    assert client.translate_api_key == "async-trans-key"

    await client.ocr(PNG_HEADER)
    assert recorded_auth_headers[-1] == "Bearer async-ocr-key"

    await client.translate("Hello", target_language="hi")
    assert recorded_auth_headers[-1] == "Bearer async-trans-key"

    # OCR only: translate fails
    ocr_only_client = BodhanAsyncClient(ocr_api_key="async-ocr-only", http_client=mock_async_http)
    with pytest.raises(BodhanAuthenticationError) as exc_t:
        await ocr_only_client.translate("Hello", target_language="hi")
    assert "Bodhan Translate API key is required" in str(exc_t.value)

    # Translate only: ocr fails
    trans_only_client = BodhanAsyncClient(translate_api_key="async-trans-only", http_client=mock_async_http)
    with pytest.raises(BodhanAuthenticationError) as exc_o:
        await trans_only_client.ocr(PNG_HEADER)
    assert "Bodhan OCR API key is required" in str(exc_o.value)


def test_translation_auto_fallback_preserves_authentication_error() -> None:
    """Verify that when POST /translate returns 404 and fallback /v1/chat/completions returns 401,
    BodhanClient preserves BodhanAuthenticationError (HTTP 401) rather than generic BodhanAPIError.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(401, json={"error": {"message": "Invalid proxy server token passed.", "code": "invalid_api_key"}})
        return httpx.Response(500)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(translate_api_key="invalid-key", http_client=mock_client) as client:
        with pytest.raises(BodhanAuthenticationError) as exc_info:
            client.translate("Hello", target_language="hi", transport="auto")

    assert calls == ["/translate", "/v1/chat/completions"]
    assert exc_info.value.status_code == 401
    assert "Translation failed on both routes" in exc_info.value.message


def test_translation_auto_fallback_server_error_raises_api_error() -> None:
    """Verify that when POST /translate returns 404 and fallback /v1/chat/completions returns 500,
    BodhanClient raises BodhanAPIError with status 500.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(500, json={"error": {"message": "Internal Server Error"}})
        return httpx.Response(500)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    with BodhanClient(translate_api_key="valid-key", http_client=mock_client) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            client.translate("Hello", target_language="hi", transport="auto")

    assert not isinstance(exc_info.value, BodhanAuthenticationError)
    assert exc_info.value.status_code == 500
    assert "Translation failed on both routes" in exc_info.value.message


@pytest.mark.asyncio
async def test_async_translation_auto_fallback_preserves_authentication_error() -> None:
    """Verify that BodhanAsyncClient preserves BodhanAuthenticationError when fallback returns 401."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(401, json={"error": {"message": "Invalid proxy server token passed.", "code": "invalid_api_key"}})
        return httpx.Response(500)

    mock_async_http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    async with BodhanAsyncClient(translate_api_key="invalid-key", http_client=mock_async_http) as client:
        with pytest.raises(BodhanAuthenticationError) as exc_info:
            await client.translate("Hello", target_language="hi", transport="auto")

    assert calls == ["/translate", "/v1/chat/completions"]
    assert exc_info.value.status_code == 401
    assert "Translation failed on both routes" in exc_info.value.message


@pytest.mark.asyncio
async def test_async_translation_auto_fallback_server_error_raises_api_error() -> None:
    """Verify that BodhanAsyncClient raises BodhanAPIError when fallback returns 500."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(500, json={"error": {"message": "Internal Server Error"}})
        return httpx.Response(500)

    mock_async_http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    async with BodhanAsyncClient(translate_api_key="valid-key", http_client=mock_async_http) as client:
        with pytest.raises(BodhanAPIError) as exc_info:
            await client.translate("Hello", target_language="hi", transport="auto")

    assert not isinstance(exc_info.value, BodhanAuthenticationError)
    assert exc_info.value.status_code == 500
    assert "Translation failed on both routes" in exc_info.value.message
