"""Unit tests for Bodhan HTTP helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bodhan_lesson.bodhan_client import get_ocr_api_key, post_json


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


def test_post_json_retries_429_then_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_BASE_URL", "https://api.bodhan.ai")

    bad = MagicMock()
    bad.ok = False
    bad.status_code = 429
    bad.text = "slow down"
    bad.json.return_value = {"error": {"code": "rate_limit", "message": "wait", "request_id": "r1"}}

    good = MagicMock()
    good.ok = True
    good.json.return_value = {"ok": True}

    with patch("bodhan_lesson.bodhan_client.requests.post", side_effect=[bad, good]) as mocked:
        with patch("bodhan_lesson.bodhan_client.time.sleep"):
            body = post_json("/v1/chat/completions", {"model": "indic-ocr"}, api_key="k", max_retries=3)

    assert body == {"ok": True}
    assert mocked.call_count == 2
