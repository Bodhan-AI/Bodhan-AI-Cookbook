"""Service-layer tests using a fake client. These check OUR request payloads and response handling.
They do not prove the live Bodhan API behaves this way."""
import base64
import json

import pytest

from app.services.audio.processing import AudioSegment
from app.services.audio.wav import parse_wav, wav_duration_seconds
from app.services.bodhan import ocr, speech, transcription, translation, transliteration
from app.services.bodhan.exceptions import (
    BodhanError, BodhanNotConfigured, BodhanProtocolError, BodhanRequestInvalid, BodhanTimeout,
)
from tests.audio_helpers import make_wav_bytes
from tests.fakes import FakeClient, json_response, wav_response

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ---------------- transcribe ----------------
def _segs(n, each=10.0):
    return [AudioSegment(i, i * each, (i + 1) * each, make_wav_bytes(0.1, rate=16000)) for i in range(n)]


async def test_transcribe_payload_and_ordering():
    texts = iter(["మొదటి", "రెండవ", "మూడవ"])
    client = FakeClient(lambda c: json_response({"text": next(texts)}))
    result = await transcription.transcribe_segments(client, list(reversed(_segs(3))), "te")
    assert result.text == "మొదటి రెండవ మూడవ"
    assert [s.index for s in result.segments] == [0, 1, 2]
    call = client.calls[0]
    assert (call["service"], call["method"], call["path"]) == ("transcribe", "POST", "/v1/audio/transcriptions")
    assert call["data"] == {"model": "indic-transcribe", "language": "te"}
    name, blob, ctype = call["files"]["file"]
    assert name.endswith(".wav") and ctype == "audio/wav" and blob[:4] == b"RIFF"
    assert "json_body" not in call or call.get("json_body") is None


async def test_transcribe_rejects_unsupported_language_before_calling():
    client = FakeClient(lambda c: json_response({"text": "x"}))
    with pytest.raises(BodhanRequestInvalid):
        await transcription.transcribe_segments(client, _segs(1), "xx")
    assert client.calls == []


async def test_transcribe_rejects_oversized_segment_before_calling():
    client = FakeClient(lambda c: json_response({"text": "x"}))
    too_long = [AudioSegment(0, 0.0, 30.5, make_wav_bytes(0.1))]
    with pytest.raises(BodhanRequestInvalid, match="limit"):
        await transcription.transcribe_segments(client, too_long, "te")
    assert client.calls == []


async def test_transcribe_failed_segment_aborts_and_names_segment():
    n = {"i": 0}

    def responder(call):
        n["i"] += 1
        if n["i"] == 2:
            return BodhanError("transcribe", "boom", status_code=415, code="unsupported")
        return json_response({"text": "ok"})

    with pytest.raises(BodhanError, match="Segment 2 of 3"):
        await transcription.transcribe_segments(FakeClient(responder), _segs(3), "te")


async def test_transcribe_bad_body_is_protocol_error():
    with pytest.raises(BodhanProtocolError):
        await transcription.transcribe_segments(FakeClient(lambda c: json_response({"nope": 1})), _segs(1), "te")


async def test_transcribe_missing_key_propagates_not_configured():
    with pytest.raises(BodhanNotConfigured):
        await transcription.transcribe_segments(
            FakeClient(lambda c: BodhanNotConfigured("transcribe", "no key")), _segs(1), "te")


# ---------------- translate ----------------
async def test_translate_payload_and_native_default():
    client = FakeClient(lambda c: json_response({"translation": "Welcome to our village.", "truncated": False,
                                                 "usage": {"prompt_tokens": 5, "completion_tokens": 4}}))
    r = await translation.translate_text(client, "నమస్కారం, మా గ్రామానికి స్వాగతం.", "en")
    assert client.calls[0]["json_body"] == {"text": "నమస్కారం, మా గ్రామానికి స్వాగతం.", "target_language": "en"}
    assert client.calls[0]["path"] == "/translate"
    assert "source_language" not in client.calls[0]["json_body"]  # no such field exists
    assert r.translation == "Welcome to our village." and not r.truncated
    assert (r.prompt_tokens, r.completion_tokens) == (5, 4)


async def test_translate_sends_script_when_not_native():
    client = FakeClient(lambda c: json_response({"translation": "namaskaram", "truncated": False}))
    await translation.translate_text(client, "hello", "te", script="roman")
    assert client.calls[0]["json_body"]["script"] == "roman"


async def test_translate_unsupported_targets_and_scripts():
    client = FakeClient(lambda c: json_response({"translation": "x"}))
    for bad in ("bhb", "bho", "zz"):
        with pytest.raises(BodhanRequestInvalid):
            await translation.translate_text(client, "hello", bad)
    with pytest.raises(BodhanRequestInvalid):
        await translation.translate_text(client, "hello", "en", script="roman")
    with pytest.raises(BodhanRequestInvalid):
        await translation.translate_text(client, "   ", "te")
    assert client.calls == []


async def test_translate_long_text_is_chunked_and_segments_retained():
    text = " ".join(f"Sentence number {i} is here." for i in range(60))
    client = FakeClient(lambda c: json_response({"translation": "T:" + c["json_body"]["text"][:12]}))
    r = await translation.translate_text(client, text, "te", max_chunk_chars=200)
    assert len(client.calls) == len(r.segments) > 3
    assert all(len(c["json_body"]["text"]) <= 200 for c in client.calls)
    assert [s.index for s in r.segments] == list(range(len(r.segments)))
    assert "".join(s.source_text + (" " if i < len(r.segments) - 1 else "")
                   for i, s in enumerate(r.segments)) == text


async def test_translate_truncated_segment_is_split_and_retried():
    def responder(call):
        t = call["json_body"]["text"]
        if len(t) > 60:
            return json_response({"translation": "cut off", "truncated": True})
        return json_response({"translation": "ok", "truncated": False})

    text = "First sentence goes here today. Second sentence goes here too. Third sentence follows soon after."
    r = await translation.translate_text(FakeClient(responder), text, "te", max_chunk_chars=500)
    assert not r.truncated
    assert len(r.segments) >= 2 and all(s.retried_split for s in r.segments)


async def test_translate_persistent_truncation_is_reported_not_hidden():
    client = FakeClient(lambda c: json_response({"translation": "partial", "truncated": True}))
    r = await translation.translate_text(client, "Short text that cannot be split further.", "te")
    assert r.truncated is True


async def test_translate_error_envelope_and_timeout_propagate():
    err = BodhanError("translate", "bad key", status_code=401, code="invalid_api_key_error")
    with pytest.raises(BodhanError) as ei:
        await translation.translate_text(FakeClient(lambda c: err), "hello", "te")
    assert ei.value.status_code == 401
    with pytest.raises(BodhanTimeout):
        await translation.translate_text(FakeClient(lambda c: BodhanTimeout("translate", "t")), "hello", "te")


async def test_translate_bad_body():
    with pytest.raises(BodhanProtocolError):
        await translation.translate_text(FakeClient(lambda c: json_response({"error": "x"})), "hello", "te")


# ---------------- transliterate ----------------
async def test_transliterate_payload_default_roman():
    client = FakeClient(lambda c: json_response({"transliteration": "Namaskaram", "language": "te",
                                                 "script": "roman", "truncated": False}))
    r = await transliteration.transliterate_text(client, "నమస్కారం", "te")
    assert client.calls[0]["json_body"] == {"text": "నమస్కారం", "language": "te", "script": "roman"}
    assert client.calls[0]["path"] == "/transliterate"
    assert r.converted_text == "Namaskaram"


async def test_transliterate_rules():
    client = FakeClient(lambda c: json_response({"transliteration": "x"}))
    with pytest.raises(BodhanRequestInvalid):
        await transliteration.transliterate_text(client, "hello", "en")
    with pytest.raises(BodhanRequestInvalid):
        await transliteration.transliterate_text(client, "నమస్కారం", "te", script="codemix")
    assert client.calls == []


async def test_transliterate_truncation_flag_surfaces():
    client = FakeClient(lambda c: json_response({"transliteration": "x", "truncated": True}))
    r = await transliteration.transliterate_text(client, "నమస్కారం", "te")
    assert r.truncated


# ---------------- speak ----------------
async def test_speech_request_shape_and_json_string_instructions():
    client = FakeClient(lambda c: wav_response(make_wav_bytes(1.0)))
    r = await speech.synthesize_speech(client, "నమస్కారం", "te", "sravani", style="happy")
    body = client.calls[0]["json_body"]
    assert body["model"] == "indic-speak" and body["voice"] == "Sravani" and body["input"] == "నమస్కారం"
    assert isinstance(body["instructions"], str)
    assert json.loads(body["instructions"]) == {"lang": "te", "style": "happy"}
    assert client.calls[0]["path"] == "/v1/audio/speech"
    assert r.wav_bytes[:4] == b"RIFF" and abs(r.duration_seconds - 1.0) < 1e-3


async def test_speech_no_style_means_lang_only():
    assert speech.build_instructions("te", None) == '{"lang": "te"}'


async def test_speech_long_text_split_and_joined_in_order():
    text = " ".join(f"Sentence {i} is spoken here." for i in range(30))
    durations = iter([1.0 + 0.1 * i for i in range(100)])
    client = FakeClient(lambda c: wav_response(make_wav_bytes(next(durations))))
    r = await speech.synthesize_speech(client, text, "en", "Vamsi", max_chunk_chars=100, gap_ms=100)
    n = len(client.calls)
    assert n > 5 and all(len(c["json_body"]["input"]) <= 100 for c in client.calls)
    expected = sum(1.0 + 0.1 * i for i in range(n)) + 0.1 * (n - 1)
    assert abs(r.duration_seconds - expected) < 0.01
    assert [s.index for s in r.segments] == list(range(n))
    fmt, _ = parse_wav(r.wav_bytes)
    assert (fmt.channels, fmt.sample_width, fmt.frame_rate) == (1, 2, 24000)


async def test_speech_json_pretending_to_be_audio_is_rejected():
    client = FakeClient(lambda c: json_response({"audio": "base64..."}))
    with pytest.raises(BodhanProtocolError, match="Expected audio/wav"):
        await speech.synthesize_speech(client, "hello", "en", "Vamsi")


async def test_speech_corrupt_wav_is_rejected():
    client = FakeClient(lambda c: wav_response(b"not a wav at all"))
    with pytest.raises(BodhanProtocolError, match="not a valid WAV"):
        await speech.synthesize_speech(client, "hello", "en", "Vamsi")


async def test_speech_incompatible_segments_refuse_to_join():
    import itertools
    rates = itertools.chain([24000], itertools.repeat(16000))  # first segment 24 kHz, the rest 16 kHz
    client = FakeClient(lambda c: wav_response(make_wav_bytes(0.5, rate=next(rates))))
    with pytest.raises(BodhanProtocolError, match="incompatible"):
        await speech.synthesize_speech(client, "One sentence here. Another sentence there.", "en", "Vamsi",
                                       max_chunk_chars=22)


async def test_speech_validation_before_calling():
    client = FakeClient(lambda c: wav_response(make_wav_bytes(0.5)))
    for kwargs in ({"language": "bho", "voice": "Vamsi"}, {"language": "te", "voice": "Nobody"},
                   {"language": "te", "voice": "Vamsi", "style": "sarcastic"}):
        with pytest.raises(BodhanRequestInvalid):
            await speech.synthesize_speech(client, "hi", **kwargs)
    with pytest.raises(BodhanRequestInvalid):
        await speech.synthesize_speech(client, "  ", "te", "Vamsi")
    assert client.calls == []


# ---------------- ocr ----------------
def _ocr_ok(text="Hello", blocks=None):
    return json_response({
        "id": "indic-ocr-1", "model": "indic-ocr", "object": "chat.completion",
        "choices": [{"finish_reason": "stop", "index": 0, "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "blocks": blocks if blocks is not None else [{"order": 1, "label": "Paragraph", "type": "Text",
                                                      "bbox_xyxy": [0, 0, 1, 1], "conf": 0.9, "text": text}],
    })


async def test_ocr_payload_structure():
    client = FakeClient(lambda c: _ocr_ok("నమస్కారం"))
    r = await ocr.ocr_image(client, PNG)
    call = client.calls[0]
    assert (call["service"], call["path"]) == ("ocr", "/v1/chat/completions")
    body = call["json_body"]
    assert body["model"] == "indic-ocr"
    assert set(body) == {"model", "messages"}  # no language, no prompt, no undocumented fields
    (msg,) = body["messages"]
    assert msg["role"] == "user" and len(msg["content"]) == 1
    part = msg["content"][0]
    assert part["type"] == "image_url"
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG
    assert r.markdown == "నమస్కారం" and r.blocks[0]["label"] == "Paragraph" and r.ok
    assert 502 not in call["retry_statuses"]


async def test_ocr_jpeg_mime_and_options():
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    client = FakeClient(lambda c: _ocr_ok())
    await ocr.ocr_image(client, jpeg, table_format="markdown", max_tokens=4096)
    body = client.calls[0]["json_body"]
    assert body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert body["table_format"] == "markdown" and body["max_tokens"] == 4096


async def test_ocr_rejects_pdf_and_junk_before_calling():
    client = FakeClient(lambda c: _ocr_ok())
    with pytest.raises(BodhanRequestInvalid):
        await ocr.ocr_image(client, b"%PDF-1.7 ...")
    with pytest.raises(BodhanRequestInvalid):
        await ocr.ocr_image(client, PNG, table_format="csv")
    assert client.calls == []


async def test_ocr_502_retries_with_higher_max_tokens():
    seen = []

    def responder(call):
        seen.append(call["json_body"].get("max_tokens"))
        if len(seen) < 3:
            return BodhanError("ocr", "failed", status_code=502, detail="block exceeded max_tokens")
        return _ocr_ok("big table")

    r = await ocr.ocr_image(FakeClient(responder), PNG)
    assert seen == [None, 4096, 8192]  # default first, then raised
    assert r.markdown == "big table" and r.attempts == 3 and r.max_tokens_used == 8192


async def test_ocr_502_retry_is_bounded_and_reraises():
    client = FakeClient(lambda c: BodhanError("ocr", "failed", status_code=502, detail="max_tokens exceeded"))
    with pytest.raises(BodhanError) as ei:
        await ocr.ocr_image(client, PNG, max_tokens_cap=4096)
    assert ei.value.status_code == 502
    assert len(client.calls) == 2  # default + one raised attempt, then stop


async def test_ocr_502_for_undecodable_image_does_not_retry():
    client = FakeClient(lambda c: BodhanError("ocr", "failed", status_code=502, detail="image could not be decoded"))
    with pytest.raises(BodhanError):
        await ocr.ocr_image(client, PNG)
    assert len(client.calls) == 1


async def test_ocr_401_is_not_retried():
    client = FakeClient(lambda c: BodhanError("ocr", "no key", status_code=401, code="401"))
    with pytest.raises(BodhanError):
        await ocr.ocr_image(client, PNG)
    assert len(client.calls) == 1


async def test_ocr_bad_response_shape():
    with pytest.raises(BodhanProtocolError):
        await ocr.ocr_image(FakeClient(lambda c: json_response({"choices": []})), PNG)


async def test_ocr_document_single_image_and_page_error_isolation(monkeypatch=None):
    client = FakeClient(lambda c: _ocr_ok("page"))
    res = await ocr.ocr_document(client, PNG)
    assert res.complete and [p.page_number for p in res.pages] == [1] and res.combined_markdown == "page"


async def test_ocr_document_pdf_pages_are_ordered_and_isolated(monkeypatch=None):
    from app.services.documents.pdf import RenderedPage
    import app.services.bodhan.ocr as ocr_mod

    pages = [RenderedPage(n, PNG + bytes([n]), 10, 10) for n in (1, 2, 3)]
    original = ocr_mod.render_pdf_pages
    ocr_mod.render_pdf_pages = lambda data, **kw: pages
    try:
        def responder(call):
            tag = base64.b64decode(call["json_body"]["messages"][0]["content"][0]["image_url"]["url"].split(",")[1])[-1]
            if tag == 2:
                return BodhanError("ocr", "page could not be transcribed", status_code=422, code="bad_page")
            return _ocr_ok(f"text of page {tag}")

        client = FakeClient(responder)
        res = await ocr.ocr_document(client, b"%PDF-1.7 fake")
    finally:
        ocr_mod.render_pdf_pages = original
    assert len(client.calls) == 3  # one request per page, never batched
    assert [p.page_number for p in res.pages] == [1, 2, 3]
    assert res.failed_pages == [2] and not res.complete
    assert res.combined_markdown == "text of page 1\n\ntext of page 3"
    assert res.pages[1].error and not res.pages[1].ok  # failure is recorded, not silently dropped


async def test_ocr_document_unsupported_format():
    with pytest.raises(BodhanRequestInvalid):
        await ocr.ocr_document(FakeClient(lambda c: _ocr_ok()), b"GIF89a....")


async def test_speech_accepts_riff_with_audio_mpeg_header():
    # Bodhan live speech endpoint returns content-type: audio/mpeg even when returning WAV PCM16 bytes
    wav_data = make_wav_bytes(0.5)
    resp = wav_response(wav_data)
    resp.headers["content-type"] = "audio/mpeg"
    client = FakeClient(lambda c: resp)
    r = await speech.synthesize_speech(client, "నమస్కారం", "te", "Sravani")
    assert r.wav_bytes == wav_data and r.language == "te" and r.voice == "Sravani"


async def test_translate_fallback_to_chat_completions_on_404():
    calls = []

    def responder(call):
        calls.append(call)
        if call["path"] == "/translate":
            return BodhanError("translate", "Not Found", status_code=404)
        if call["path"] == "/v1/chat/completions":
            return json_response({
                "id": "chatcmpl-1",
                "model": "indic-translate",
                "choices": [{"finish_reason": "stop", "index": 0, "message": {"role": "assistant", "content": "Hello world"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            })
        raise ValueError(f"Unexpected path: {call['path']}")

    client = FakeClient(responder)
    r = await translation.translate_text(client, "నమస్కారం", "en")
    assert r.translation == "Hello world"
    assert len(calls) == 2
    assert calls[0]["path"] == "/translate"
    assert calls[1]["path"] == "/v1/chat/completions"
    assert calls[1]["json_body"]["model"] == "indic-translate"


async def test_transliterate_fallback_to_chat_completions_on_404():
    calls = []

    def responder(call):
        calls.append(call)
        if call["path"] == "/transliterate":
            return BodhanError("transliterate", "Not Found", status_code=404)
        if call["path"] == "/v1/chat/completions":
            return json_response({
                "id": "chatcmpl-2",
                "model": "indic-transliterate",
                "choices": [{"finish_reason": "stop", "index": 0, "message": {"role": "assistant", "content": "Namaskaram"}}],
            })
        raise ValueError(f"Unexpected path: {call['path']}")

    client = FakeClient(responder)
    r = await transliteration.transliterate_text(client, "నమస్కారం", "te", script="roman")
    assert r.converted_text == "Namaskaram"
    assert len(calls) == 2
    assert calls[0]["path"] == "/transliterate"
    assert calls[1]["path"] == "/v1/chat/completions"
    assert calls[1]["json_body"]["model"] == "indic-transliterate"

