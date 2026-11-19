"""Client tests with a fake HTTP server (httpx.MockTransport). No network, no real keys."""

import io
import json
import wave

import httpx
import pytest

from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import load_settings
from bodhan_mcp.costs import BudgetExceeded, CostTracker
from bodhan_mcp.errors import BodhanError, ConfigError

KEYS = {"BODHAN_API_KEY": "test-key"}


def make_wav(seconds: float = 1.0, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def build(handler, tmp_path, *, tracker=None, env=None):
    sleeps: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(handler))
    settings = load_settings({**KEYS, "BODHAN_CACHE_DIR": str(tmp_path / "cache"), **(env or {})})
    client = BodhanClient(settings, http=http, tracker=tracker, sleep=sleeps.append)
    return client, sleeps


def test_translate_falls_back_to_chat_route_and_remembers(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "नमस्ते"}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 5},
        })

    client, _ = build(handler, tmp_path)
    assert client.translate("Hello", "hi")["text"] == "नमस्ते"
    client.translate("Goodbye", "hi")
    assert calls == ["/translate", "/v1/chat/completions", "/v1/chat/completions"]  # 404 tried once


def test_translate_flags_truncation(tmp_path):
    handler = lambda req: httpx.Response(200, json={  # noqa: E731
        "translation": "abc", "truncated": True, "usage": {"completion_tokens": 3}})
    client, _ = build(handler, tmp_path)
    assert client.translate("long text", "ta")["truncated"] is True


def test_retries_rate_limit_with_backoff_then_succeeds(tmp_path):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        if n["c"] < 3:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json={"translation": "ok", "usage": {"completion_tokens": 1}})

    client, sleeps = build(handler, tmp_path)
    assert client.translate("hi", "ta")["text"] == "ok"
    assert n["c"] == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0] - 0.3  # waits grow


def test_bad_request_is_not_retried(tmp_path):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(400, json={"error": {"message": "bad", "code": "unsupported_language"}})

    client, sleeps = build(handler, tmp_path)
    with pytest.raises(BodhanError) as e:
        client.translate("hi", "bho")
    assert e.value.code == "unsupported_language" and n["c"] == 1 and sleeps == []


def test_gives_up_after_max_attempts(tmp_path):
    handler = lambda req: httpx.Response(502, json={"detail": "upstream"})  # noqa: E731
    client, sleeps = build(handler, tmp_path)
    with pytest.raises(BodhanError) as e:
        client.translate("hi", "ta")
    assert e.value.code == "upstream_error" and len(sleeps) == 3  # 4 attempts, 3 waits


def test_second_identical_call_is_free_and_skips_network(tmp_path):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(200, json={"translation": "x", "usage": {"completion_tokens": 10}})

    client, _ = build(handler, tmp_path)
    first = client.translate("same", "ta")
    spent = client.tracker.total
    second = client.translate("same", "ta")
    assert n["c"] == 1 and second["cached"] is True and not first["cached"]
    assert client.tracker.total == spent


def test_speak_sends_instructions_as_json_string_and_charges_by_chars(tmp_path):
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, content=make_wav(), headers={"content-type": "audio/wav"})

    client, _ = build(handler, tmp_path)
    audio = client.speak("a" * 1000, "Kavya", "hi")
    assert audio.startswith(b"RIFF")
    assert isinstance(seen["instructions"], str) and json.loads(seen["instructions"]) == {"lang": "hi"}
    assert client.tracker.total == pytest.approx(0.6)  # 1000 chars x Rs 6 per 10,000


def test_budget_blocks_call_before_any_network_use(tmp_path):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(200, content=make_wav())

    client, _ = build(handler, tmp_path, tracker=CostTracker(max_cost_inr=0.5))
    with pytest.raises(BudgetExceeded) as e:
        client.speak("a" * 1000, "Kavya", "hi")  # would cost Rs 0.6
    assert e.value.code == "budget_exceeded" and n["c"] == 0 and client.tracker.total == 0


def test_speak_rejects_non_wav_response(tmp_path):
    client, _ = build(lambda req: httpx.Response(200, content=b"not audio"), tmp_path)
    with pytest.raises(BodhanError) as e:
        client.speak("hello", "Kavya", "hi")
    assert e.value.code == "bad_audio"


def test_transcribe_charges_by_audio_length(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(make_wav(seconds=6.0))
    client, _ = build(lambda req: httpx.Response(200, json={"text": "नमस्ते"}), tmp_path)
    out = client.transcribe(wav, "hi")
    assert out["text"] == "नमस्ते" and out["seconds"] == 6.0
    assert client.tracker.total == pytest.approx(0.01)  # 6 s x Rs 0.10 per minute


def test_ocr_returns_text_and_blocks(tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG fake")

    def handler(req):
        body = json.loads(req.content)
        assert body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Page text"}}],
            "blocks": [{"order": 1, "label": "Paragraph", "text": "Page text"}],
            "usage": {"total_tokens": 10},
        })

    client, _ = build(handler, tmp_path)
    out = client.ocr(img)
    assert out["text"] == "Page text" and out["blocks"][0]["label"] == "Paragraph"
    assert client.tracker.total == pytest.approx(0.20)


def test_missing_key_fails_before_network(tmp_path):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(200, json={})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BodhanClient(load_settings({"BODHAN_CACHE_DIR": str(tmp_path)}), http=http)
    with pytest.raises(ConfigError):
        client.translate("hi", "ta")
    assert n["c"] == 0


def test_auth_error_right_after_success_is_retried(tmp_path):
    seq = iter([200, 401, 200])

    def handler(req):
        s = next(seq)
        if s == 401:
            return httpx.Response(401, json={"error": {"message": "blip"}})
        return httpx.Response(200, json={"translation": "ok", "usage": {"completion_tokens": 1}})

    client, _ = build(handler, tmp_path)
    client.translate("one", "ta")
    assert client.translate("two", "ta")["text"] == "ok"  # the 401 was treated as transient