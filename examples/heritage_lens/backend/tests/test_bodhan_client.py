"""BodhanClient over httpx.MockTransport. Verifies headers, bodies, retries and error parsing.
These are mocked: they do NOT prove the live API behaves this way."""
import httpx
import pytest

from app.services.bodhan.client import BodhanClient
from app.services.bodhan.exceptions import BodhanError, BodhanNetworkError, BodhanNotConfigured, BodhanTimeout

KEY = "secret-key-abcdef123456"


async def _no_sleep(_):
    sleeps.append(_)


sleeps: list[float] = []


def make(handler, key=KEY, retries=2):
    sleeps.clear()
    return BodhanClient(base_url="https://api.test", key_resolver=lambda s: key, max_retries=retries,
                        transport=httpx.MockTransport(handler), sleep=_no_sleep)


async def test_bearer_header_json_body_and_parsing():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.content
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"translation": "hi"}, headers={"x-request-id": "r1"})

    resp = await make(handler).request("translate", "POST", "/translate", json_body={"text": "x", "target_language": "en"})
    assert seen["auth"] == f"Bearer {KEY}" and seen["url"] == "https://api.test/translate"
    assert b'"target_language"' in seen["body"]
    assert resp.json == {"translation": "hi"} and resp.request_id == "r1"


async def test_multipart_contains_model_language_and_file():
    seen = {}

    def handler(req):
        seen["ctype"], seen["body"] = req.headers["content-type"], req.content
        return httpx.Response(200, json={"text": "ok"})

    await make(handler).request("transcribe", "POST", "/v1/audio/transcriptions",
                                data={"model": "indic-transcribe", "language": "te"},
                                files={"file": ("a.wav", b"RIFFdata", "audio/wav")})
    assert seen["ctype"].startswith("multipart/form-data")
    for needle in (b'name="model"', b"indic-transcribe", b'name="language"', b"te", b'filename="a.wav"', b"RIFFdata"):
        assert needle in seen["body"]


async def test_binary_audio_response_is_preserved():
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 30
    resp = await make(lambda r: httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})).request(
        "speak", "POST", "/v1/audio/speech", json_body={})
    assert resp.content == wav and resp.content_type == "audio/wav" and resp.json is None


async def test_missing_key_never_hits_network():
    called = []
    c = make(lambda r: called.append(r) or httpx.Response(200, json={}), key=None)
    with pytest.raises(BodhanNotConfigured):
        await c.request("ocr", "POST", "/x")
    assert called == []


async def test_401_not_retried_and_key_not_leaked():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": f"Invalid key {KEY}", "code": "invalid_api_key_error"}})

    with pytest.raises(BodhanError) as ei:
        await make(handler).request("translate", "POST", "/translate", json_body={})
    assert len(calls) == 1 and ei.value.status_code == 401 and ei.value.code == "invalid_api_key_error"
    assert KEY not in str(ei.value) and KEY not in ei.value.message


async def test_503_retried_then_succeeds_with_backoff():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(503, json={"error": {"message": "busy"}}) if n["i"] < 3 else httpx.Response(200, json={"ok": 1})

    resp = await make(handler).request("translate", "POST", "/t", json_body={})
    assert resp.json == {"ok": 1} and n["i"] == 3 and sleeps == [0.5, 1.0]


async def test_retries_are_bounded():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(500, json={"error": {"message": "down"}})

    with pytest.raises(BodhanError):
        await make(handler, retries=2).request("translate", "POST", "/t", json_body={})
    assert n["i"] == 3


async def test_retry_after_header_is_honoured():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow"}}) if n["i"] == 1 \
            else httpx.Response(200, json={})

    await make(handler).request("translate", "POST", "/t", json_body={})
    assert sleeps == [3.0]


async def test_timeout_retried_then_raised():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(BodhanTimeout):
        await make(handler).request("speak", "POST", "/s", json_body={})
    assert n["i"] == 3


async def test_network_error():
    def handler(req):
        raise httpx.ConnectError("nope", request=req)

    with pytest.raises(BodhanNetworkError):
        await make(handler, retries=0).request("speak", "POST", "/s", json_body={})


async def test_excluded_502_is_not_retried_by_client():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(502, json={"detail": "block exceeded max_tokens"})

    with pytest.raises(BodhanError) as ei:
        await make(handler).request("ocr", "POST", "/v1/chat/completions", json_body={},
                                    retry_statuses={429, 500, 503, 504})
    assert n["i"] == 1 and ei.value.status_code == 502 and "max_tokens" in ei.value.detail


async def test_both_documented_error_envelopes_parse():
    with pytest.raises(BodhanError) as a:
        await make(lambda r: httpx.Response(400, json={"error": {"message": "bad", "code": "invalid_request",
                                                                  "request_id": "abc"}})).request("translate", "POST", "/t")
    assert (a.value.code, a.value.request_id, a.value.message) == ("invalid_request", "abc", "bad")
    with pytest.raises(BodhanError) as b:
        await make(lambda r: httpx.Response(422, json={"detail": "language unsupported"})).request("translate", "POST", "/t")
    assert b.value.message == "language unsupported"
    with pytest.raises(BodhanError) as c:
        await make(lambda r: httpx.Response(415, text="<html>nope</html>")).request("transcribe", "POST", "/t")
    assert c.value.status_code == 415


async def test_403_forbidden_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(403, json={"error": {"message": "Forbidden", "code": "permission_denied"}})

    with pytest.raises(BodhanError) as ei:
        await make(handler).request("ocr", "POST", "/v1/chat/completions", json_body={})
    assert len(calls) == 1 and ei.value.status_code == 403


async def test_service_specific_key_resolution():
    keys = {
        "speak": "test-key-speak-1111111111111",
        "transcribe": "test-key-transcribe-2222222222",
        "translate": "test-key-translate-3333333333",
        "transliterate": "test-key-transliterate-444444",
        "ocr": "test-key-ocr-5555555555555",
    }
    seen_auth = {}

    def handler(req):
        svc_name = req.url.path.lstrip("/")
        seen_auth[svc_name] = req.headers.get("authorization")
        return httpx.Response(200, json={"ok": 1})

    client = BodhanClient(
        base_url="https://api.test",
        key_resolver=lambda s: keys.get(s),
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )

    for svc in keys:
        await client.request(svc, "POST", f"/{svc}", json_body={})

    for svc in keys:
        assert seen_auth[svc] == f"Bearer {keys[svc]}"

