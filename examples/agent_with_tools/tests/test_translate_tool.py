import httpx
import pytest

from bodhan_mcp.tools import translate as translate_module
from bodhan_mcp.tools.translate import translate


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-key")
    monkeypatch.setenv("BODHAN_BASE_URL", "https://example.test")
    translate_module._CACHE.clear()


def _transport(handler):
    return httpx.MockTransport(handler)


def _chat(text: str, tokens: int, truncated: bool = False) -> httpx.Response:
    """A /v1/chat/completions style response."""
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "length" if truncated else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": tokens, "total_tokens": tokens + 1},
        },
    )


async def test_request_format_matches_docs():
    import json

    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return _chat("ok", 1)

    await translate("The meeting has been postponed.", "kn", transport=_transport(handler))
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["body"] == {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "The meeting has been postponed."}],
        "target_language_code": "kn",
        "target_script": "native",
    }


async def test_success_computes_cost():
    def handler(req: httpx.Request):
        assert req.headers["authorization"] == "Bearer test-key"
        return _chat("सुप्रभात", 100)

    r = await translate("Good morning", "hi", transport=_transport(handler))
    assert r.ok and r.translation == "सुप्रभात"
    assert r.cost_inr == pytest.approx(100 * 0.20 / 10_000)


async def test_unsupported_language_makes_no_request():
    def handler(req):
        raise AssertionError("must not call the API")

    r = await translate("Bonjour", "fr", transport=_transport(handler))
    assert not r.ok and r.error.code == "unsupported_language"


async def test_empty_text():
    r = await translate("  ", "hi")
    assert r.error.code == "empty_text"


async def test_retries_on_429_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429)
        return _chat("x", 1)

    r = await translate("hi", "ta", transport=_transport(handler), backoff_s=0)
    assert r.ok and len(calls) == 3


async def test_gives_up_after_max_attempts():
    r = await translate("hi", "ta", transport=_transport(lambda req: httpx.Response(503)), backoff_s=0)
    assert not r.ok and r.error.retryable


async def test_api_error_envelope_passthrough():
    body = {"error": {"message": "bad", "code": "invalid_script", "request_id": "r"}}
    r = await translate("hi", "en", "roman", transport=_transport(lambda req: httpx.Response(400, json=body)))
    assert r.error.code == "invalid_script" and not r.error.retryable


def _echo_upper(req: httpx.Request):
    """Fake API: 'translates' by upper-casing, 1 token per 10 chars."""
    import json

    text = json.loads(req.content)["messages"][0]["content"]
    return _chat(text.upper(), max(1, len(text) // 10))


async def test_long_text_is_chunked_in_order_and_layout_kept():
    sizes = []

    def handler(req):
        import json

        sizes.append(len(json.loads(req.content)["messages"][0]["content"]))
        return _echo_upper(req)

    text = "\n\n".join(f"Sentence number {i} is here. And another one." for i in range(40))
    r = await translate(text, "hi", transport=_transport(handler), max_chars=200)
    assert r.ok and r.chunks == len(sizes) > 1
    assert max(sizes) <= 200
    assert r.translation == text.upper()  # order and paragraph breaks preserved
    assert r.cost_inr > 0


async def test_too_large_is_halved_until_it_fits():
    import json

    def handler(req):
        if len(json.loads(req.content)["messages"][0]["content"]) > 60:
            return httpx.Response(413, json={"error": {"code": "payload_too_large", "message": "too big", "request_id": "r"}})
        return _echo_upper(req)

    text = " ".join(f"Sentence {i}." for i in range(30))
    r = await translate(text, "ta", transport=_transport(handler), max_chars=1500)
    assert r.ok and r.translation == text.upper() and r.chunks > 1


async def test_truncated_output_is_split_and_retried():
    import json

    def handler(req):
        text = json.loads(req.content)["messages"][0]["content"]
        return _chat(text.upper(), 5, truncated=len(text) > 80)

    text = " ".join(f"Sentence {i}." for i in range(20))
    r = await translate(text, "mr", transport=_transport(handler))
    assert r.ok and not r.truncated and r.translation == text.upper()


async def test_same_chunk_is_never_sent_twice():
    calls = []

    def handler(req):
        calls.append(1)
        return _echo_upper(req)

    transport = _transport(handler)
    await translate("Hello there.", "hi", transport=transport)
    r = await translate("Hello there.", "hi", transport=transport)
    assert len(calls) == 1 and r.translation == "HELLO THERE."


async def test_failure_midway_reports_which_chunk():
    import json

    def handler(req):
        if "FAIL" in json.loads(req.content)["messages"][0]["content"]:
            return httpx.Response(400, json={"error": {"code": "invalid_request_error", "message": "nope", "request_id": "r"}})
        return _echo_upper(req)

    r = await translate("ok one. FAIL two. ok three.", "hi", transport=_transport(handler), max_chars=10)
    assert not r.ok and r.translation is None and "Chunk" in r.error.message


async def test_text_above_hard_ceiling_is_rejected():
    r = await translate("a " * 150_000, "hi")
    assert r.error.code == "text_too_long"
