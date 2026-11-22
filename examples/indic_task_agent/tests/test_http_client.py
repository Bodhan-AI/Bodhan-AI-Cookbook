import json

import httpx
import pytest

from bodhan_mcp.client import OcrResult, SpeechResult, TranscribeResult, TranslateResult
from bodhan_mcp.http_client import BodhanApiError, HttpBodhanClient


def _client(handler, **keys):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return HttpBodhanClient(client=http, **keys)


def test_translate_maps_chat_response_and_uses_confirmed_route():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer translate-secret"
        body = json.loads(request.content)
        assert body["model"] == "indic-translate"
        assert body["target_language"] == "mr"
        assert body["messages"] == [{"role": "user", "content": "The school is closed."}]
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "सोमवारी शाळा बंद राहील.", "role": "assistant"},
            }],
            "usage": {"completion_tokens": 9},
        })

    client = _client(handler, translate_key="translate-secret")
    assert client.translate("The school is closed.", "mr") == TranslateResult(
        text="सोमवारी शाळा बंद राहील.",
        truncated=False,
    )


def test_translate_marks_length_termination_as_truncated():
    client = _client(
        lambda request: httpx.Response(200, json={
            "choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
        }),
        translate_key="key",
    )
    assert client.translate("source", "hi").truncated


def test_speech_detects_wav_from_bytes_not_mislabeled_header():
    wav = b"RIFF\x24\x00\x00\x00WAVEdata"

    def handler(request):
        assert request.url.path == "/v1/audio/speech"
        body = json.loads(request.content)
        assert body["instructions"] == '{"lang": "hi"}'
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=wav)

    client = _client(handler, speak_key="speak-secret")
    assert client.synthesize("नमस्ते", "hi", "Kavya") == SpeechResult(wav, "wav")


def test_http_errors_keep_actionable_api_message():
    client = _client(
        lambda request: httpx.Response(404, json={"detail": "Not Found"}),
        translate_key="key",
    )
    with pytest.raises(BodhanApiError, match="HTTP 404: Not Found"):
        client.translate("text", "mr")


def test_missing_key_fails_before_network_request():
    def unexpected_request(request):
        raise AssertionError("request should not be sent")

    client = _client(unexpected_request)
    with pytest.raises(RuntimeError, match="BODHAN_TRANSLATE_KEY"):
        client.translate("text", "mr")


def test_transcribe_maps_text_response(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"test audio")

    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert b"indic-transcribe" in request.content
        assert b"voice.wav" in request.content
        return httpx.Response(200, json={"text": "नमस्ते"})

    client = _client(handler, transcribe_key="key")
    assert client.transcribe(audio) == TranscribeResult("नमस्ते")


def test_ocr_maps_chat_response(tmp_path):
    image = tmp_path / "page.png"
    image.write_bytes(b"fake png bytes")

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "indic-ocr"
        image_url = payload["messages"][0]["content"][0]["image_url"]["url"]
        assert image_url.startswith("data:image/png;base64,")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "| A | B |\n|---|---|\n| 1 | 2 |"}}],
        })

    client = _client(handler, ocr_key="key")
    assert client.ocr(image) == OcrResult(markdown="| A | B |\n|---|---|\n| 1 | 2 |")