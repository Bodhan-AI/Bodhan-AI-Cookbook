import json

import httpx
import pytest
import respx

from bodhan_mcp import core
from bodhan_mcp.core import ToolError
from bodhan_mcp.tools import audio, ocr, speak, translate
from conftest import make_wav, wav_bytes

BASE = "https://api.bodhan.ai"


# ---- audio ----
@pytest.mark.parametrize("dur", [5, 29.9, 30, 31, 180, 3600])
def test_plan_cuts_never_exceeds_limit(dur):
    cuts = audio.plan_cuts(dur, [])
    assert cuts[0][0] == 0 and cuts[-1][1] == pytest.approx(dur, abs=0.01)
    assert all(b - a <= audio.MAX_CHUNK_S + 1e-6 for a, b in cuts)
    assert all(cuts[i][1] == cuts[i + 1][0] for i in range(len(cuts) - 1))  # contiguous


def test_plan_cuts_prefers_silence():
    cuts = audio.plan_cuts(60, [(20.0, 21.0), (40, 41)])
    assert cuts[0][1] == 20.5


def test_split_cues_time_sums():
    cues = audio.split_cues("एक वाक्य। दूसरा वाक्य। तीसरा वाक्य। चौथा वाक्य।", 0, 28)
    assert len(cues) > 1 and cues[0]["start"] == 0 and cues[-1]["end"] == pytest.approx(28, abs=0.01)


def test_repetitive():
    assert audio.repetitive("a b c d " * 10) and not audio.repetitive("one two three four five six seven eight")


@respx.mock
async def test_transcribe_chunks_long_audio_and_caches(env):
    f = make_wav(env / "long.wav", 65, rate=16000, tone=True)
    route = respx.post(f"{BASE}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={"text": "यह एक परीक्षण है।"}))
    r = await audio.transcribe_audio(str(f), language="hi")
    assert r["chunks"] >= 3 and route.call_count == r["chunks"] and r["cost_inr"] > 0
    assert all(b - a <= 28.01 for a, b in [(s["start"], s["end"]) for s in json.loads(open(r["segments_ref"]).read())])
    n = route.call_count
    r2 = await audio.transcribe_audio(str(f), language="hi")  # same input: no paid calls
    assert route.call_count == n and r2["cost_inr"] == 0


async def test_transcribe_unsupported_language_fails_fast(env):
    f = make_wav(env / "a.wav", 2)
    with pytest.raises(ToolError) as e:
        await audio.transcribe_audio(str(f), language="fr")
    assert e.value.code == "UNSUPPORTED_LANGUAGE"


def test_path_traversal_rejected(env):
    with pytest.raises(ToolError) as e:
        core.resolve_in("/etc/passwd")
    assert e.value.code == "PATH_NOT_ALLOWED"
    with pytest.raises(ToolError) as e:
        core.resolve_in(str(env / "nope.wav"))
    assert e.value.code == "FILE_NOT_FOUND"


def test_build_srt(env):
    p = core.write_text("s.json", json.dumps([{"start": 0, "end": 2, "text": "hello"}, {"start": 1, "end": 1.2, "text": "overlap"}]))
    r = audio.build_srt(str(p))
    txt = open(r["srt_ref"]).read()
    assert "00:00:00,000 --> 00:00:02,000" in txt and r["cues"] == 2
    assert "00:00:02,000 --> 00:00:02,500" in txt  # overlap pushed forward, min 0.5 s


# ---- translate ----
def test_split_text_respects_limit():
    t = ("वाक्य एक। " * 500) + "x" * 3000
    parts = translate.split_text(t, 1200)
    assert all(len(p) <= 1200 for p in parts) and len(parts) > 3


async def test_translate_unsupported_before_any_call(env):
    with pytest.raises(ToolError) as e:
        await translate.translate_text(text="hello", target_language="fr")
    assert e.value.code == "UNSUPPORTED_LANGUAGE"
    with pytest.raises(ToolError):
        await translate.translate_text(text="hello", target_language="bho")


@respx.mock
async def test_translate_truncated_triggers_resplit(env):
    calls = []

    def handler(req):
        t = json.loads(req.content)["messages"][0]["content"]
        calls.append(t)
        return httpx.Response(200, json={"choices": [{"finish_reason": "length" if len(t) > 60 else "stop",
                                                      "message": {"content": "ता:" + t[:3]}}], "usage": {"completion_tokens": 5}})
    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=handler)
    r = await translate.translate_text(text="This is a long sentence. " * 8, target_language="ta")
    assert r["resplit_pieces"] >= 1 and len(calls[0]) > 60 and min(len(c) for c in calls) <= 60  # halved until it fit


@respx.mock
async def test_translate_markdown_keeps_table(env):
    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=lambda req: httpx.Response(
        200, json={"choices": [{"finish_reason": "stop", "message": {"content": "MR(" + json.loads(req.content)["messages"][0]["content"] + ")"}}],
                   "usage": {"completion_tokens": 3}}))
    md = "# Title\n\n| Name | Qty |\n|---|---|\n| Apple | 3 |\n\nHello world"
    r = await translate.translate_text(text=md, target_language="mr", mode="markdown")
    out = open(r["translation_ref"]).read()
    assert "|---|---|" in out and "| MR(Name) | MR(Qty) |" in out and "| MR(Apple) | 3 |" in out and out.startswith("# MR(Title)")
    assert r["warnings"] == []


@respx.mock
async def test_translate_same_language_makes_no_call(env):
    route = respx.post(f"{BASE}/v1/chat/completions")
    r = await translate.translate_text(text="This is English text.", target_language="en")
    assert route.call_count == 0 and r["cost_inr"] == 0


# ---- speak ----
@respx.mock
async def test_speak_chunks_joins_and_labels(env):
    route = respx.post(f"{BASE}/v1/audio/speech").mock(return_value=httpx.Response(200, content=wav_bytes(1.0)))
    text = " ".join(f"यह वाक्य संख्या {i} है जो बोला जाएगा।" for i in range(30))
    r = await speak.synthesize_speech(text=text, language="hi", gender="male", format="wav")
    assert r["chunks"] == route.call_count > 1 and r["ai_generated"] and r["voice"] == "Amit"
    assert r["duration_s"] == pytest.approx(r["chunks"] * 1.15, abs=0.1)
    body = json.loads(route.calls[0].request.content)
    assert json.loads(body["instructions"]) == {"lang": "hi"} and len(body["input"]) <= speak.CHUNK_CHARS


@respx.mock
async def test_speak_mp3_and_budget_refusal(env, monkeypatch):
    respx.post(f"{BASE}/v1/audio/speech").mock(return_value=httpx.Response(200, content=wav_bytes(1.0)))
    r = await speak.synthesize_speech(text="नमस्ते दोस्तों।", language="hi")
    assert r["audio_ref"].endswith(".mp3") and audio.inspect_media(r["audio_ref"])["kind"] == "audio"
    monkeypatch.setenv("BODHAN_MAX_COST_INR", "0.01")
    with pytest.raises(ToolError) as e:
        await speak.synthesize_speech(text="एक बिल्कुल नया लंबा वाक्य जो पैसे खर्च करेगा। " * 5, language="hi")
    assert e.value.code == "BUDGET_EXCEEDED"


@respx.mock
async def test_speak_identical_chunks_synthesised_once(env):
    route = respx.post(f"{BASE}/v1/audio/speech").mock(return_value=httpx.Response(200, content=wav_bytes(1.0)))
    r = await speak.synthesize_speech(text="\n".join(["वही वाक्य।"] * 4), language="hi", format="wav")
    assert r["chunks"] == 4 and route.call_count == 1


async def test_speak_validation(env):
    for kw, code in [({"language": "fr", "text": "x"}, "UNSUPPORTED_LANGUAGE"), ({"language": "hi", "text": "  "}, "EMPTY_INPUT"),
                     ({"language": "hi", "text": "x", "voice": "Nobody"}, "INVALID_ARGUMENT"),
                     ({"language": "hi", "text": "x", "style": "bogus"}, "INVALID_ARGUMENT")]:
        with pytest.raises(ToolError) as e:
            await speak.synthesize_speech(**kw)
        assert e.value.code == code


# ---- ocr ----
def _pdf(path, pages):
    import pymupdf as fitz
    d = fitz.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"page {i + 1}")
    d.save(path)


@respx.mock
async def test_ocr_pdf_one_image_per_request_and_502_retry(env):
    _pdf(env / "d.pdf", 3)
    seen = []

    def handler(req):
        body = json.loads(req.content)
        assert len(body["messages"]) == 1 and len(body["messages"][0]["content"]) == 1
        seen.append(body["max_tokens"])
        if body["max_tokens"] == 2048 and len(seen) == 2:
            return httpx.Response(502, json={"error": {"message": "block exceeded max_tokens"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "text"}}], "blocks": [{"conf": 0.9}]})
    route = respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=handler)
    r = await ocr.ocr_document(str(env / "d.pdf"))
    assert r["pages_done"] == 3 and 4096 in seen and route.call_count == 4
    assert "<!-- page 3 -->" in open(r["markdown_ref"]).read()


def test_ocr_page_ranges_and_errors(env):
    assert ocr.parse_pages("1-3,7", 10) == [1, 2, 3, 7]
    with pytest.raises(ToolError):
        ocr.parse_pages("9", 3)
    with pytest.raises(ToolError):
        ocr.parse_pages("a-b", 3)


async def test_ocr_corrupt_image(env):
    (env / "bad.png").write_bytes(b"not an image")
    with pytest.raises(ToolError) as e:
        await ocr.ocr_document(str(env / "bad.png"))
    assert e.value.code == "UNSUPPORTED_FORMAT"


# ---- http ----
@respx.mock
async def test_retry_on_429_then_success_and_no_retry_on_400(env):
    route = respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=[httpx.Response(429), httpx.Response(200, json={})])
    r = await core.post("translate", "/v1/chat/completions", json={})
    assert r.status_code == 200 and route.call_count == 2
    route.mock(side_effect=None, return_value=httpx.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(ToolError) as e:
        await core.post("translate", "/v1/chat/completions", json={})
    assert e.value.code == "INVALID_ARGUMENT" and route.call_count == 3


async def test_missing_key_is_structured(env, monkeypatch):
    monkeypatch.delenv("BODHAN_TRANSLATE_KEY")
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    with pytest.raises(ToolError) as e:
        await core.post("translate", "/v1/chat/completions", json={})
    assert e.value.code == "MISSING_API_KEY"


def test_shared_key_stands_in_for_an_unset_model_key(env, monkeypatch):
    monkeypatch.delenv("BODHAN_SPEAK_KEY")
    monkeypatch.setenv("BODHAN_API_KEY", "shared")
    assert core.api_key("speak") == "shared" and core.api_key("translate") != "shared"  # a model's own key still wins


@respx.mock
@pytest.mark.parametrize("resp", [httpx.Response(402, json={"error": {"message": "payment required"}}),
                                  httpx.Response(429, json={"error": {"message": "You have no credits remaining"}})])
async def test_out_of_credit_is_distinct_and_not_retried(env, resp):
    route = respx.post(f"{BASE}/v1/chat/completions").mock(return_value=resp)
    with pytest.raises(ToolError) as e:
        await core.post("translate", "/v1/chat/completions", json={})
    assert e.value.code == "INSUFFICIENT_CREDITS" and route.call_count == 1 and not e.value.retryable


@respx.mock
async def test_plain_rate_limit_still_retries(env):
    route = respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=[httpx.Response(429, json={"error": {"message": "slow down"}}), httpx.Response(200, json={})])
    assert (await core.post("translate", "/v1/chat/completions", json={})).status_code == 200 and route.call_count == 2


def test_script_variant_names_resolve_to_their_codes():
    from bodhan_mcp.languages import resolve
    assert [resolve(n) for n in ("Kashmiri (Devanagari)", "Sindhi (Perso-Arabic)", "Manipuri (Bengali script)", "Sindhi (Devanagari)")] \
        == ["ks-Deva", "sd-Arab", "mni-Beng", "sd"]
