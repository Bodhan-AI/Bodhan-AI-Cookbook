import base64
import json

import httpx
import pytest
from bodhan_mcp.bodhan import BodhanError, Budget, BudgetExceeded
from conftest import make_client, wav_bytes


def test_translate_sends_documented_body_and_returns_result(tmp_path):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"translation": "नमस्ते", "target_language": "hi", "script": "native", "truncated": False,
                                         "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}})

    client = make_client(tmp_path, handler)
    out = client.translate("Hello", target_language="hi")
    assert out["translation"] == "नमस्ते" and out["truncated"] is False
    assert seen["body"] == {"text": "Hello", "target_language": "hi", "script": "native"}
    assert seen["auth"] == "Bearer test-key"


def test_same_input_is_never_sent_twice(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"translation": "ok", "truncated": False, "usage": {"completion_tokens": 1}})

    client = make_client(tmp_path, handler)
    client.translate("Same text", target_language="ta")
    client.translate("Same text", target_language="ta")
    assert len(calls) == 1
    assert [e["cached"] for e in client.ledger] == [False, True]


def test_retries_rate_limit_then_succeeds(tmp_path):
    attempts = []

    def handler(req):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, json={"error": {"message": "slow down", "code": "rate_limit_exceeded_error"}})
        return httpx.Response(200, json={"translation": "ok", "truncated": False, "usage": {"completion_tokens": 1}})

    client = make_client(tmp_path, handler)
    assert client.translate("x", target_language="hi")["translation"] == "ok"
    assert len(attempts) == 3


def test_client_error_is_not_retried_and_keeps_code(tmp_path):
    attempts = []

    def handler(req):
        attempts.append(1)
        return httpx.Response(400, json={"error": {"message": "bad", "code": "unsupported_language", "request_id": "req_1"}})

    client = make_client(tmp_path, handler)
    with pytest.raises(BodhanError) as err:
        client.translate("x", target_language="bhb")
    assert err.value.code == "unsupported_language" and not err.value.retryable
    assert len(attempts) == 1
    assert "req_1" in err.value.message


def test_gives_up_after_max_attempts_on_server_errors(tmp_path):
    client = make_client(tmp_path, lambda req: httpx.Response(503, json={"error": {"message": "down", "code": "internal_error"}}), max_attempts=3)
    with pytest.raises(BodhanError) as err:
        client.translate("x", target_language="hi")
    assert err.value.code == "internal_error" and err.value.retryable


def test_missing_key_is_a_clear_error(tmp_path):
    client = make_client(tmp_path, lambda req: httpx.Response(200), env={"BODHAN_API_KEY": ""})
    with pytest.raises(BodhanError) as err:
        client.translate("x", target_language="hi")
    assert err.value.code == "missing_key" and "BODHAN_TRANSLATE_KEY" in err.value.message


def test_per_model_key_takes_priority(tmp_path):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"translation": "ok", "truncated": False, "usage": {"completion_tokens": 1}})

    client = make_client(tmp_path, handler, env={"BODHAN_TRANSLATE_KEY": "specific-key"})
    client.translate("x", target_language="hi")
    assert seen["auth"] == "Bearer specific-key"


def test_budget_stops_before_overspending(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"text": "ok"})

    client = make_client(tmp_path, handler, budget=Budget(limit_inr=0.5))
    # 60 minutes of audio at Rs 0.10/min = Rs 6, far over the Rs 0.5 limit
    with pytest.raises(BudgetExceeded):
        client.transcribe(b"RIFF", filename="a.wav", language="hi", duration_s=3600)
    assert calls == []


def test_speak_returns_base64_wav_and_charges_per_character(tmp_path):
    audio = wav_bytes(0.5)
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=audio, headers={"content-type": "audio/wav"})

    budget = Budget()
    client = make_client(tmp_path, handler, budget=budget)
    res = client.speak("कक्षा नौ बजे शुरू होती है।", voice="Kavya", lang="hi")
    assert base64.b64decode(res["wav_b64"]) == audio
    assert json.loads(seen["body"]["instructions"]) == {"lang": "hi"}
    assert seen["body"]["voice"] == "Kavya" and seen["body"]["model"] == "indic-speak"
    assert budget.spent > 0


def test_ocr_sends_one_data_uri_and_returns_blocks(tmp_path):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "# Title"}, "finish_reason": "stop", "index": 0}],
            "blocks": [{"order": 0, "label": "Title", "type": "Title", "bbox_xyxy": [0, 0, 1, 1], "conf": 0.9, "text": "Title"}],
        })

    client = make_client(tmp_path, handler)
    res = client.ocr(b"\x89PNGfake", mime="image/png")
    assert res["text"] == "# Title" and res["finish_reason"] == "stop" and len(res["blocks"]) == 1
    url = seen["body"]["messages"][0]["content"][0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_translate_falls_back_to_chat_route_on_404_and_stays_there(tmp_path):
    paths = []

    def handler(req):
        paths.append(req.url.path)
        if req.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        body = json.loads(req.content)
        assert body["model"] == "indic-translate" and len(body["messages"]) == 1
        assert body["messages"][0]["content"].startswith("Translate the following text into Tamil.")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "வணக்கம்"}, "finish_reason": "stop"}],
                                         "usage": {"completion_tokens": 4}})

    client = make_client(tmp_path, handler)
    first = client.translate("Hello", target_language="ta")
    second = client.translate("Good morning", target_language="ta")
    assert first["translation"] == "வணக்கம்" and first["route"] == "/v1/chat/completions"
    assert second["route"] == "/v1/chat/completions"
    assert paths == ["/translate", "/v1/chat/completions", "/v1/chat/completions"]  # /translate is not retried after the 404


def test_chat_fallback_marks_length_cutoff_as_truncated(tmp_path):
    def handler(req):
        if req.url.path == "/translate":
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "आधा"}, "finish_reason": "length"}], "usage": {"completion_tokens": 9}})

    assert make_client(tmp_path, handler).translate("A long text", target_language="hi")["truncated"] is True


def test_chat_fallback_refuses_roman_script_clearly(tmp_path):
    client = make_client(tmp_path, lambda req: httpx.Response(404, json={"detail": "Not Found"}))
    with pytest.raises(BodhanError) as err:
        client.translate("Hello", target_language="hi", script="roman")
    assert err.value.code == "unsupported_on_fallback"


def test_rate_limit_backs_off_longer_and_honours_retry_after(tmp_path):
    sleeps, attempts = [], []

    def handler(req):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, json={"detail": "Maximum rate limit reached for the model"}, headers={"retry-after": "12"})
        if len(attempts) == 2:
            return httpx.Response(400, json={"detail": "Maximum rate limit reached for the model"})
        return httpx.Response(200, json={"translation": "ok", "truncated": False, "usage": {"completion_tokens": 1}})

    client = make_client(tmp_path, handler)
    client._sleep = sleeps.append  # record waits instead of sleeping
    assert client.translate("x", target_language="hi")["translation"] == "ok"
    assert len(attempts) == 3 and client.rate_limit_waits == 2
    assert sleeps[0] >= 11.9  # Retry-After honoured
    assert sleeps[1] >= 9.0  # second rate limit without header: 10 s backoff


def test_rate_limit_gives_up_with_clear_message(tmp_path):
    client = make_client(tmp_path, lambda req: httpx.Response(429, json={"detail": "Maximum rate limit reached for the model"}))
    with pytest.raises(BodhanError) as err:
        client.translate("x", target_language="hi")
    assert err.value.code == "rate_limited" and "try again later" in err.value.message


def test_identical_parallel_calls_are_sent_once(tmp_path):
    import threading
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    calls, gate = [], threading.Event()

    def handler(req):
        calls.append(1)
        gate.wait(timeout=2)
        return httpx.Response(200, json={"text": "ok"})

    client = make_client(tmp_path, handler)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(client.transcribe, b"same-audio", filename="a.wav", language="hi", duration_s=5) for _ in range(3)]
        _time.sleep(0.2)
        gate.set()
        results = [f.result()["text"] for f in futures]
    assert results == ["ok", "ok", "ok"] and len(calls) == 1
    assert sorted(e["cached"] for e in client.ledger) == [False, True, True]


def test_template_placeholders_are_not_used_as_keys(tmp_path):
    client = make_client(tmp_path, lambda req: httpx.Response(200), env={"BODHAN_API_KEY": "<optional-single-key-for-all-models>",
                                                                         "BODHAN_TRANSLATE_KEY": "<your-indic-translate-key>"})
    with pytest.raises(BodhanError) as err:
        client.translate("x", target_language="hi")
    assert err.value.code == "missing_key"
