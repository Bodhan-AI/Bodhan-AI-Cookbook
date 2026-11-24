"""Unit tests for Bodhan HTTP helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bodhan_lesson.bodhan_client import (
    get_ocr_api_key,
    post_json,
    _is_credit_exhausted,
    _retry_wait_s,
)


def test_get_ocr_api_key_prefers_ocr_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_OCR_KEY", "ocr-key")
    monkeypatch.setenv("BODHAN_API_KEY", "generic-key")
    assert get_ocr_api_key() == "ocr-key"


def test_get_ocr_api_key_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BODHAN_OCR_KEY", raising=False)
    monkeypatch.setenv("BODHAN_API_KEY", "generic-key")
    assert get_ocr_api_key() == "generic-key"


def test_get_ocr_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BODHAN_OCR_KEY", raising=False)
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BODHAN_OCR_KEY"):
        get_ocr_api_key()


def test_retry_wait_honors_retry_after_header() -> None:
    response = MagicMock()
    response.status_code = 429
    response.headers = {"Retry-After": "7"}
    assert _retry_wait_s(response, attempt=0) == 7.0


def test_retry_wait_caps_retry_after() -> None:
    response = MagicMock()
    response.status_code = 429
    response.headers = {"Retry-After": "999"}
    assert _retry_wait_s(response, attempt=0) == 120.0


def test_retry_wait_429_without_header_uses_floor() -> None:
    response = MagicMock()
    response.status_code = 429
    response.headers = {}
    assert _retry_wait_s(response, attempt=0) == 2.0
    assert _retry_wait_s(response, attempt=1) == 4.0


def test_is_credit_exhausted() -> None:
    response = MagicMock()
    response.status_code = 429
    response.text = "no credit left"
    response.json.return_value = {
        "error": {"code": "rate_limit_exceeded_error", "message": "Insufficient credit"}
    }
    assert _is_credit_exhausted(response) is True

    response.json.return_value = {
        "error": {"code": "rate_limit_exceeded_error", "message": "Maximum rate limit reached"}
    }
    response.text = "Maximum rate limit reached"
    assert _is_credit_exhausted(response) is False


def test_post_json_retries_429_then_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_BASE_URL", "https://api.bodhan.ai")

    bad = MagicMock()
    bad.ok = False
    bad.status_code = 429
    bad.text = "slow down"
    bad.headers = {"Retry-After": "3"}
    bad.json.return_value = {
        "error": {
            "code": "rate_limit_exceeded_error",
            "message": "Maximum rate limit reached",
            "request_id": "r1",
        }
    }

    good = MagicMock()
    good.ok = True
    good.json.return_value = {"ok": True}

    with patch("bodhan_lesson.bodhan_client.requests.post", side_effect=[bad, good]) as mocked:
        with patch("bodhan_lesson.bodhan_client.time.sleep") as slept:
            body = post_json(
                "/v1/chat/completions",
                {"model": "indic-ocr"},
                api_key="k",
                max_retries=3,
            )

    assert body == {"ok": True}
    assert mocked.call_count == 2
    slept.assert_called_once_with(3.0)


def test_post_json_does_not_retry_credit_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_BASE_URL", "https://api.bodhan.ai")

    bad = MagicMock()
    bad.ok = False
    bad.status_code = 429
    bad.text = "out of credit"
    bad.headers = {"Retry-After": "5"}
    bad.json.return_value = {
        "error": {
            "code": "rate_limit_exceeded_error",
            "message": "Insufficient credit on this key",
            "request_id": "r2",
        }
    }

    with patch("bodhan_lesson.bodhan_client.requests.post", return_value=bad) as mocked:
        with patch("bodhan_lesson.bodhan_client.time.sleep") as slept:
            with pytest.raises(RuntimeError, match="Insufficient credit"):
                post_json("/translate", {"text": "x"}, api_key="k", max_retries=5)

    assert mocked.call_count == 1
    slept.assert_not_called()
