"""Client, cache and budget with a fake HTTP transport: no network, no spend."""

import asyncio
import json

import httpx
import pytest

from bodhan_mcp.bodhan import Bodhan, BodhanError, Budget, cache_key


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for m in ("TRANSCRIBE", "TRANSLATE", "SPEAK", "OCR"):
        monkeypatch.setenv(f"BODHAN_{m}_KEY", f"test-{m.lower()}")
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)


def chat(content, tokens=10, finish="stop"):
    return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}],
                                     "usage": {"completion_tokens": tokens}})


def client(tmp_path, responses, limit=float("inf")):
    """Bodhan client whose transport replays `responses` (Responses or exceptions); returns (client, requests)."""
    seen, queue = [], list(responses)

    def handler(request):
        seen.append(request)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    return Bodhan(budget=Budget(limit), cache_dir=tmp_path, backoff_s=0, transport=httpx.MockTransport(handler)), seen


def run(coro):
    return asyncio.run(coro)


def test_cache_key():
    k = cache_key("indic-speak", {"voice": "Kavya", "lang": "hi"}, b"x")
    assert k == cache_key("indic-speak", {"lang": "hi", "voice": "Kavya"}, b"x")  # param order irrelevant
    assert k != cache_key("indic-speak", {"voice": "Amit", "lang": "hi"}, b"x")
    assert k != cache_key("indic-speak", {"voice": "Kavya", "lang": "hi"}, b"y")
    assert k != cache_key("indic-ocr", {"voice": "Kavya", "lang": "hi"}, b"x")


def test_budget():
    b = Budget(0.25)
    b.reserve("indic-ocr", 0.20)
    with pytest.raises(BodhanError, match="budget_exceeded"):
        b.reserve("indic-ocr", 0.20)  # the first reservation counts
    b.settle("indic-ocr", 0.20, 0.10)
    assert b.spent_inr == pytest.approx(0.10)


def test_translate_request_cost_and_cache(tmp_path):
    c, seen = client(tmp_path, [chat("नमस्ते", tokens=500)])
    assert run(c.translate("Hello", "hi")) == "नमस्ते"
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/chat/completions" and seen[0].headers["authorization"] == "Bearer test-translate"
    assert body == {"model": "indic-translate", "messages": [{"role": "user", "content": "Hello"}], "target_language": "hi"}
    assert c.budget.spent_inr == pytest.approx(0.01)  # 500 output tokens
    assert run(c.translate("Hello", "hi")) == "नमस्ते"  # second time: cache, no request, no cost
    assert len(seen) == 1 and c.budget.spent_inr == pytest.approx(0.01) and c.budget.calls[-1]["cached"]


def test_speak_returns_cached_bytes(tmp_path):
    c, seen = client(tmp_path, [httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"ID3")])
    assert run(c.speak("नमस्ते", "Kavya", "hi", style="news")) == b"ID3"
    assert json.loads(json.loads(seen[0].content)["instructions"]) == {"lang": "hi", "style": "news"}
    assert run(c.speak("नमस्ते", "Kavya", "hi", style="news")) == b"ID3" and len(seen) == 1


def test_retries_then_succeeds(tmp_path):
    c, seen = client(tmp_path, [httpx.Response(429), httpx.ReadTimeout("slow"), httpx.Response(503), chat("ok")])
    assert run(c.translate("Hi", "ta")) == "ok" and len(seen) == 4


def test_gives_up_and_refunds(tmp_path):
    c, seen = client(tmp_path, [httpx.Response(500)] * 4)
    with pytest.raises(BodhanError) as e:
        run(c.translate("Hi", "ta"))
    assert e.value.code == "unavailable" and e.value.retryable and len(seen) == 4 and c.budget.spent_inr == 0


@pytest.mark.parametrize("status,body,code", [
    (400, {"error": {"message": "unsupported language 'xx'"}}, "unsupported_language"),
    (401, {"error": {"message": "bad key"}}, "auth_failed"),
    (422, {"error": {"message": "nope"}}, "bad_request"),
])
def test_4xx_not_retried(tmp_path, status, body, code):
    c, seen = client(tmp_path, [httpx.Response(status, json=body)])
    with pytest.raises(BodhanError) as e:
        run(c.ocr(b"\x89PNG", "p.png"))
    assert e.value.code == code and len(seen) == 1


def test_bad_input_rejected_before_any_request(tmp_path):
    c, seen = client(tmp_path, [], limit=0.10)
    for coro, code in [
        (c.translate("Bonjour", "fr"), "unsupported_language"),
        (c.speak("नमस्ते", "NoSuchVoice", "hi"), "bad_request"),
        (c.transcribe(b"x", "clip.m4a", 3.0), "unsupported_media"),
        (c.ocr(b"%PDF", "doc.pdf"), "unsupported_media"),
        (c.ocr(b"\x89PNG", "p.png"), "budget_exceeded"),  # Rs 0.20 > Rs 0.10
    ]:
        with pytest.raises(BodhanError) as e:
            run(coro)
        assert e.value.code == code
    assert seen == []


def test_missing_key_then_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("BODHAN_OCR_KEY")
    c, seen = client(tmp_path, [chat("text")])
    with pytest.raises(BodhanError, match="missing_key"):
        run(c.ocr(b"\x89PNG", "p.png"))
    monkeypatch.setenv("BODHAN_API_KEY", "shared")
    assert run(c.ocr(b"\x89PNG", "p.png")) == "text" and seen[0].headers["authorization"] == "Bearer shared"


def test_truncated_translation_is_an_error(tmp_path):
    c, _ = client(tmp_path, [chat("आधा", finish="length")])
    with pytest.raises(BodhanError, match="truncated"):
        run(c.translate("long text", "hi"))
