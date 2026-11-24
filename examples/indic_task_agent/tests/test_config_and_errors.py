"""Tests for settings loading and error conversion. No network, no real keys."""

import pytest

from bodhan_mcp.config import load_settings, normalize_base_url
from bodhan_mcp.errors import BodhanError, ConfigError


@pytest.mark.parametrize(
    "raw",
    [
        "https://api.bodhan.ai",
        "https://api.bodhan.ai/",
        "https://api.bodhan.ai/v1",
        "https://api.bodhan.ai/v1/",
        '  "https://api.bodhan.ai"  ',
        "",
        "<placeholder>",
        None,
    ],
)
def test_base_url_is_normalized(raw):
    assert normalize_base_url(raw) == "https://api.bodhan.ai"


def test_per_model_key_beats_shared_key():
    s = load_settings({"BODHAN_API_KEY": "shared", "BODHAN_SPEAK_KEY": "speak-key"})
    assert s.key_for("speak") == "speak-key"
    assert s.key_for("ocr") == "shared"


def test_placeholders_are_ignored():
    s = load_settings({"BODHAN_API_KEY": "<your key>", "BODHAN_OCR_KEY": "real"})
    assert s.key_for("ocr") == "real"
    with pytest.raises(ConfigError) as e:
        s.key_for("speak")
    assert "BODHAN_SPEAK_KEY" in str(e.value)  # message names the variable to set


def test_missing_key_is_a_structured_error():
    err = ConfigError("x")
    assert err.to_dict()["error"]["code"] == "missing_api_key"
    assert err.retryable is False


def test_translate_style_error_envelope():
    body = {"error": {"message": "bad lang", "code": "unsupported_language", "request_id": "r1"}}
    e = BodhanError.from_response("translate", 400, body)
    assert (e.code, e.request_id, e.retryable) == ("unsupported_language", "r1", False)
    assert e.hint  # actionable advice present


def test_auth_failures_share_one_code_across_apis():
    ocr = {"error": {"message": "no key", "type": "auth_error", "code": "401"}}
    translate = {"error": {"message": "bad key", "code": "invalid_api_key_error"}}
    assert BodhanError.from_response("ocr", 401, ocr).code == "invalid_api_key"
    assert BodhanError.from_response("translate", 401, translate).code == "invalid_api_key"


def test_numeric_code_falls_back_to_status():
    body = {"error": {"message": "too big", "code": "413"}}
    assert BodhanError.from_response("translate", 413, body).code == "payload_too_large"


def test_gateway_detail_style():
    e = BodhanError.from_response("translate", 404, {"detail": "Not Found"})
    assert e.code == "not_found" and e.message == "Not Found"


@pytest.mark.parametrize("status,retry", [(429, True), (502, True), (500, True), (400, False), (401, False)])
def test_retryable_by_status(status, retry):
    assert BodhanError.from_response("speak", status, {}).retryable is retry