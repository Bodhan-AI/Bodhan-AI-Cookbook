from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from conftest import make_settings, no_sleep, speech_wav

from bodhan_mcp.config import api_key
from bodhan_mcp.core.client import BodhanClient
from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.utils.telemetry import call_scope

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SERVER_500 = {"error": {"message": "Internal server error", "type": "internal_server_error"}}
OCR_TRUNCATED = {
    "error": {
        "message": "litellm.BadGatewayError: {'detail': 'the recognizer did not return a usable transcription "
        "for 1 of 2 blocks (order 1: truncated); the page was not transcribed'}",
        "code": "502",
    }
}
OCR_OK = {
    "id": "indic-ocr-1",
    "choices": [{"message": {"content": "पाठ"}, "finish_reason": "stop"}],
    "blocks": [{"order": 0, "label": "Paragraph", "text": "पाठ"}, "not-a-block"],
}


async def _call(tmp_path: Path, route_path: str, responses: list[httpx.Response], call):  # type: ignore[no-untyped-def]
    settings = make_settings(tmp_path)
    async with BodhanClient(settings, sleep=no_sleep) as client:
        with respx.mock(base_url=settings.base_url, assert_all_called=False) as mock, call_scope() as calls:
            route = mock.post(route_path).mock(side_effect=responses)
            try:
                result = await call(client)
                error = None
            except BodhanToolError as exc:
                result, error = None, exc
    return result, error, route, calls


async def test_translate_sends_target_and_records_cost(tmp_path: Path) -> None:
    body = {"id": "t1", "choices": [{"message": {"content": "नमस्ते"}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 10}}  # fmt: skip
    result, error, route, calls = await _call(
        tmp_path, "/v1/chat/completions", [httpx.Response(200, json=body)], lambda c: c.translate("Hello", "hi")
    )
    assert error is None and result.text == "नमस्ते"
    sent = json.loads(route.calls[0].request.content)
    assert sent["target_language"] == "hi" and sent["model"] == "indic-translate"
    assert calls[0].estimated_cost_inr > 0


async def test_missing_key_fails_before_any_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY")
    _, error, route, _ = await _call(tmp_path, "/v1/chat/completions", [], lambda c: c.translate("Hello", "hi"))
    assert error.code == "missing_api_key" and route.call_count == 0
    assert "BODHAN_API_KEY" in error.hint


def test_shared_key_is_fallback_and_model_key_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY")
    assert api_key("translate") is None
    monkeypatch.setenv("BODHAN_API_KEY", "shared-key")
    assert api_key("translate") == "shared-key"
    assert api_key("speak") == "test-key"


async def test_transcribe_vendor_rejection_is_not_retried(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(speech_wav(0.5, 16_000))
    rejected = {"error": {"message": "Error code: 502 - {'detail': 'the ASR vendor rejected the request'}"}}
    _, error, route, _ = await _call(
        tmp_path, "/v1/audio/transcriptions", [httpx.Response(502, json=rejected)] * 3,
        lambda c: c.transcribe(audio, 0.5),
    )  # fmt: skip
    assert error.code == "upstream_error" and not error.retryable and route.call_count == 1


async def test_transcribe_generic_502_is_retried(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(speech_wav(0.5, 16_000))
    responses = [
        httpx.Response(502, json={"error": {"message": "bad gateway"}}),
        httpx.Response(200, json={"text": "हाँ"}),
    ]
    result, error, route, calls = await _call(
        tmp_path, "/v1/audio/transcriptions", responses, lambda c: c.transcribe(audio, 30.0, language="hi")
    )
    assert error is None and result.text == "हाँ" and route.call_count == 2
    assert b'name="language"' in route.calls[1].request.content
    assert calls[0].estimated_cost_inr == pytest.approx(0.05)


async def test_speak_500_is_retried_once_only(tmp_path: Path) -> None:
    _, error, route, _ = await _call(
        tmp_path, "/v1/audio/speech", [httpx.Response(500, json=SERVER_500)] * 3,
        lambda c: c.speak("नमस्ते", "Kavya", "hi"),
    )  # fmt: skip
    assert error.code == "upstream_error" and not error.retryable and route.call_count == 2


async def test_speak_accepts_mislabelled_wav_and_sends_json_instructions(tmp_path: Path) -> None:
    wav = httpx.Response(200, content=speech_wav(0.2), headers={"content-type": "audio/mpeg"})
    result, error, route, _ = await _call(
        tmp_path, "/v1/audio/speech", [wav], lambda c: c.speak("नमस्ते", "Kavya", "hi", style="news")
    )
    assert error is None and result.audio[:4] == b"RIFF"
    sent = json.loads(route.calls[0].request.content)
    assert json.loads(sent["instructions"]) == {"lang": "hi", "style": "news"}


async def test_speak_rejects_non_audio_body(tmp_path: Path) -> None:
    _, error, _, _ = await _call(
        tmp_path, "/v1/audio/speech", [httpx.Response(200, json={"x": 1})], lambda c: c.speak("a", "Kavya", "hi")
    )
    assert error.code == "upstream_error"


async def test_ocr_null_body_is_unsupported_file(tmp_path: Path) -> None:
    image = tmp_path / "p.png"
    image.write_bytes(PNG)
    _, error, route, calls = await _call(
        tmp_path, "/v1/chat/completions", [httpx.Response(200, content=b"null")], lambda c: c.ocr(image)
    )
    assert error.code == "unsupported_file" and not error.retryable and route.call_count == 1
    assert calls[0].billing_uncertain


async def test_ocr_truncation_is_marked_and_not_retried(tmp_path: Path) -> None:
    image = tmp_path / "p.png"
    image.write_bytes(PNG)
    _, error, route, _ = await _call(
        tmp_path, "/v1/chat/completions", [httpx.Response(502, json=OCR_TRUNCATED)] * 3, lambda c: c.ocr(image)
    )
    assert error.code == "incomplete_result" and error.details["truncated"] is True and route.call_count == 1


async def test_ocr_parses_blocks_and_rejects_non_images(tmp_path: Path) -> None:
    image = tmp_path / "p.png"
    image.write_bytes(PNG)
    result, error, route, calls = await _call(
        tmp_path, "/v1/chat/completions", [httpx.Response(200, json=OCR_OK)], lambda c: c.ocr(image, max_tokens=4096)
    )
    assert error is None and result.markdown == "पाठ" and len(result.blocks) == 1
    sent = json.loads(route.calls[0].request.content)
    assert sent["max_tokens"] == 4096 and sent["messages"][0]["content"][0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert calls[0].estimated_cost_inr == pytest.approx(0.2)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 ...")
    _, error, route, _ = await _call(tmp_path, "/v1/chat/completions", [], lambda c: c.ocr(pdf))
    assert error.code == "unsupported_file" and route.call_count == 0


async def test_rate_limit_is_retried(tmp_path: Path) -> None:
    responses = [httpx.Response(429, json={"detail": "Maximum rate limit reached for the model"})] * 2 + [
        httpx.Response(200, content=speech_wav(0.2))
    ]
    result, error, route, _ = await _call(tmp_path, "/v1/audio/speech", responses, lambda c: c.speak("a", "Amit", "hi"))
    assert error is None and route.call_count == 3
