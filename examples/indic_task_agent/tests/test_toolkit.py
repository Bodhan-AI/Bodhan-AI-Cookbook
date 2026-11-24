"""Toolkit tests with a fake Bodhan API (httpx.MockTransport). No network, no cost."""

import io
import json
import shutil
import subprocess
import wave

import httpx
import pymupdf
import pytest

from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import load_settings
from bodhan_mcp.costs import BudgetExceeded, CostTracker
from bodhan_mcp.errors import BodhanError
from bodhan_mcp.toolkit import Toolkit
from bodhan_mcp.verify import compare_texts, normalize_for_compare

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def wav_bytes(seconds=1.0, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds))
    return buf.getvalue()


class FakeAPI:
    """Pretends to be Bodhan. Records every request so tests can inspect them."""

    def __init__(self):
        self.requests = []
        self.translate_fn = lambda text, n: {"translation": "नमस्ते दुनिया", "usage": {"completion_tokens": 5}}
        self.transcribe_fn = lambda n: "नमस्ते दुनिया"
        self.ocr_fn = lambda body, n: httpx.Response(200, json={
            "choices": [{"message": {"content": f"Page text {n}"}}], "blocks": [{"order": 1}], "usage": {}})

    def count(self, path):
        return sum(1 for p, _ in self.requests if p == path)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        body = None if path == "/v1/audio/transcriptions" else json.loads(req.content or b"{}")
        self.requests.append((path, body))
        n = self.count(path)
        if path == "/translate":
            return httpx.Response(200, json=self.translate_fn(body["text"], n))
        if path == "/v1/audio/speech":
            return httpx.Response(200, content=wav_bytes(1.0))
        if path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": self.transcribe_fn(n)})
        if path == "/v1/chat/completions":
            return self.ocr_fn(body, n)
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture
def api():
    return FakeAPI()


@pytest.fixture
def kit(tmp_path, api):
    def make(budget=None):
        settings = load_settings({"BODHAN_API_KEY": "k", "BODHAN_CACHE_DIR": str(tmp_path / "cache")})
        client = BodhanClient(settings, http=httpx.Client(transport=httpx.MockTransport(api)),
                              tracker=CostTracker(budget), sleep=lambda s: None)
        return Toolkit(client, tmp_path / "out")
    return make


# ---- local tools ------------------------------------------------------------------------
def test_language_support_and_french(kit):
    k = kit()
    assert k.language_support("Marathi")["speak"] is True
    assert k.language_support("French")["recognized"] is False


def test_write_subtitles_srt_format(kit):
    k = kit()
    r = k.write_subtitles([{"start": 1.5, "end": 3.25, "text": "Hello"},
                           {"start": 3725.001, "end": 3727.0, "text": "Late"}])
    body = open(r["path"], encoding="utf-8").read()
    assert "00:00:01,500 --> 00:00:03,250" in body and "01:02:05,001 --> 01:02:07,000" in body


def test_write_subtitles_rejects_bad_segments(kit):
    with pytest.raises(BodhanError) as e:
        kit().write_subtitles([{"start": 1}])
    assert e.value.code == "invalid_segments"


def test_outputs_cannot_escape_workspace(kit):
    k = kit()
    for bad in ("../../evil.srt", "/tmp/evil.srt"):
        with pytest.raises(BodhanError) as e:
            k.write_subtitles([{"start": 0, "end": 1, "text": "x"}], bad)
        assert e.value.code == "path_outside_workspace"


# ---- translate --------------------------------------------------------------------------
def test_translate_unsupported_language_makes_no_api_call(kit, api):
    k = kit()
    for lang in ("French", "Bhojpuri"):
        with pytest.raises(BodhanError) as e:
            k.translate_text(lang, text="hello")
        assert e.value.code == "unsupported_language"
    assert api.requests == []


def test_translate_keeps_paragraphs_and_checks_script(kit, api):
    r = kit().translate_text("hi", text="First paragraph.\n\nSecond paragraph.")
    assert r["api_calls"] == 2 and r["script_ok"] is True
    assert "\n\n" in r["text"]


def test_translate_long_table_is_split_by_rows(kit, api):
    table = "\n".join(f"| row{i} | value{i} |" for i in range(120))
    k = kit()
    r = k.translate_text("hi", text=table)
    sent = [b["text"] for p, b in api.requests if p == "/translate"]
    assert len(sent) > 1 and all(len(s) <= 1000 for s in sent)
    assert all(not s.startswith(" ") and s.startswith("|") for s in sent)  # rows intact
    assert r["ok"]


def test_translate_retries_truncated_output_by_halving(kit, api):
    def fn(text, n):
        return {"translation": "x", "truncated": len(text) > 60, "usage": {"completion_tokens": 1}}
    api.translate_fn = fn
    r = kit().translate_text("hi", text="word " * 40)  # 200 chars
    assert r["truncation_retries"] >= 1 and r["api_calls"] >= 3


def test_translate_giant_truncation_fails_clearly(kit, api):
    api.translate_fn = lambda text, n: {"translation": "x", "truncated": True, "usage": {}}
    with pytest.raises(BodhanError) as e:
        kit().translate_text("hi", text="word " * 40)
    assert e.value.code == "translation_truncated"


def test_long_translation_goes_to_a_file(kit, api):
    api.translate_fn = lambda text, n: {"translation": "ab " * 1200, "usage": {"completion_tokens": 1}}
    r = kit().translate_text("hi", text="hello")
    assert "text_file" in r and "text" not in r and r["chars"] > 3000
    assert open(r["text_file"], encoding="utf-8").read().startswith("ab ab")


def test_translate_from_text_file(kit, tmp_path):
    f = tmp_path / "in.txt"
    f.write_text("Hello there", encoding="utf-8")
    assert kit().translate_text("hi", text_file=str(f))["ok"]


def test_translate_needs_input(kit):
    with pytest.raises(BodhanError) as e:
        kit().translate_text("hi")
    assert e.value.code == "missing_input"


# ---- speak ------------------------------------------------------------------------------
def test_speak_splits_text_and_joins_audio(kit, api):
    text = " ".join(f"यह वाक्य संख्या {i} है।" for i in range(60))
    r = kit().speak_text("hi", text=text, out_path="out.wav")
    n = api.count("/v1/audio/speech")
    assert n == r["pieces"] and n > 1
    with wave.open(r["path"]) as w:
        assert w.getframerate() == 24000
        assert w.getnframes() / 24000 > n  # n seconds of speech plus gaps
    assert r["voice"] == "Kavya"


def test_speak_rejects_bad_language_voice_style_format(kit, api):
    k = kit()
    for kwargs, code in [({"language": "French", "text": "x"}, "unsupported_language"),
                         ({"language": "hi", "text": "x", "voice": "Nobody"}, "invalid_voice"),
                         ({"language": "hi", "text": "x", "style": "sarcastic"}, "invalid_style"),
                         ({"language": "hi", "text": "x", "out_path": "a.ogg"}, "unsupported_format"),
                         ({"language": "hi", "text": "   "}, "empty_text")]:
        with pytest.raises(BodhanError) as e:
            k.speak_text(**kwargs)
        assert e.value.code == code
    assert api.requests == []


def test_speak_budget_blocks_before_any_call(kit, api):
    with pytest.raises(BudgetExceeded):
        kit(budget=0.01).speak_text("hi", text="वाक्य " * 200)
    assert api.requests == []


def test_speak_does_not_read_markdown_symbols(kit, api):
    kit().speak_text("hi", text="## शीर्षक\n**मोटा** पाठ")
    spoken = [b["input"] for p, b in api.requests if p == "/v1/audio/speech"]
    assert all("#" not in s and "*" not in s for s in spoken)


@needs_ffmpeg
def test_speak_can_write_mp3(kit):
    r = kit().speak_text("hi", text="नमस्ते", out_path="hello.mp3")
    assert r["format"] == "mp3" and open(r["path"], "rb").read(3) in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


# ---- verify -----------------------------------------------------------------------------
def test_compare_ignores_punctuation_but_keeps_vowel_signs():
    assert normalize_for_compare("बैठक कल, स्थगित है।") == "बैठक कल स्थगित है"
    assert compare_texts("बैठक कल स्थगित है।", "बैठक कल स्थगित है")["passed"] is True


def test_compare_detects_omission_and_repetition():
    exp = "one two three four five six seven eight"
    omitted = compare_texts(exp, "one two three four")
    repeated = compare_texts(exp, exp + " " + exp)
    assert omitted["passed"] is False and omitted["word_recall"] == 0.5
    assert repeated["passed"] is False and repeated["word_precision"] == 0.5


@needs_ffmpeg
def test_verify_speech_pass_and_fail(kit, api, tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(wav_bytes(2.0, 16000))
    api.transcribe_fn = lambda n: "नमस्ते दुनिया"
    assert kit().verify_speech(str(audio), expected_text="नमस्ते दुनिया।", language="hi")["passed"] is True
    audio2 = tmp_path / "b.wav"
    audio2.write_bytes(wav_bytes(3.0, 16000))  # different bytes, so no cache hit
    api.transcribe_fn = lambda n: "कुछ और ही"
    bad = kit().verify_speech(str(audio2), expected_text="नमस्ते दुनिया।", language="hi")
    assert bad["passed"] is False


# ---- OCR --------------------------------------------------------------------------------
def make_pdf(path, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    doc.save(path)


def test_ocr_pdf_reads_each_page(kit, api, tmp_path):
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, 3)
    r = kit().ocr_document(str(pdf))
    assert api.count("/v1/chat/completions") == 3 and r["pages_read"] == 3
    assert open(r["text_file"], encoding="utf-8").read().count("Page text") == 3
    url = api.requests[0][1]["messages"][0]["content"][0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")



# ---- passing text between tools by file path ----------------------------------------------
def test_every_text_result_is_also_a_file(kit, api):
    r = kit().translate_text("hi", text="Hello")
    assert r["text"] == open(r["text_file"], encoding="utf-8").read()  # same content, by path


def test_save_text_copies_a_result_into_deliverables_without_retyping(kit, api):
    k = kit()
    r = k.translate_text("hi", text="Hello")
    saved = k.save_text("answer.hi.md", text_file=r["text_file"])
    assert open(saved["path"], encoding="utf-8").read() == r["text"]
    assert saved["path"].endswith("answer.hi.md") and "artifacts" in saved["path"]


def test_save_text_rejects_odd_extensions_and_escapes(kit):
    k = kit()
    for path, code in [("run.exe", "unsupported_format"), ("../../x.txt", "path_outside_workspace")]:
        with pytest.raises(BodhanError) as e:
            k.save_text(path, text="x")
        assert e.value.code == code


def test_translate_segments_keeps_timings_and_makes_a_file(kit, api):
    api.translate_fn = lambda text, n: {"translation": "नमस्ते", "usage": {"completion_tokens": 2}}
    segs = [{"start": 0.0, "end": 2.5, "text": "Hello"}, {"start": 2.5, "end": 5.0, "text": "World"},
            {"start": 5.0, "end": 6.0, "text": ""}]
    r = kit().translate_segments("hi", segments=segs)
    assert [(s["start"], s["end"]) for s in r["segments"]] == [(0.0, 2.5), (2.5, 5.0), (5.0, 6.0)]
    assert r["segments"][2]["text"] == "" and r["api_calls"] == 2  # empty segment costs nothing
    assert json.loads(open(r["segments_file"], encoding="utf-8").read()) == r["segments"]


def test_translate_segments_rejects_unsupported_language_and_bad_input(kit, api):
    k = kit()
    with pytest.raises(BodhanError) as e:
        k.translate_segments("French", segments=[{"start": 0, "end": 1, "text": "x"}])
    assert e.value.code == "unsupported_language"
    with pytest.raises(BodhanError) as e:
        k.translate_segments("hi", segments=[{"start": 0, "text": "x"}])
    assert e.value.code == "invalid_segments"
    assert api.requests == []


def test_subtitles_from_segments_file(kit, tmp_path):
    f = tmp_path / "segs.json"
    f.write_text(json.dumps([{"start": 0, "end": 1.5, "text": "Hi"}]), encoding="utf-8")
    r = kit().write_subtitles(segments_file=str(f), out_path="a.srt")
    assert "00:00:00,000 --> 00:00:01,500" in open(r["path"], encoding="utf-8").read()


@needs_ffmpeg
def test_transcribe_always_returns_a_segments_file(kit, api, tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(wav_bytes(2.0, 16000))
    r = kit().transcribe_audio(str(audio), "hi")
    assert json.loads(open(r["segments_file"], encoding="utf-8").read())[0]["text"] == "नमस्ते दुनिया"
    assert r["text_file"] and r["segment_count"] == 1


def test_ocr_respects_max_pages_and_reports_skipped(kit, api, tmp_path):
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, 5)
    r = kit().ocr_document(str(pdf), max_pages=2)
    assert r["pages_read"] == 2 and r["pages_skipped"] == 3 and r["pages_total"] == 5


def test_ocr_retries_with_more_tokens_after_502(kit, api, tmp_path):
    api.ocr_fn = lambda body, n: (httpx.Response(200, json={"choices": [{"message": {"content": "big table"}}], "blocks": []})
                                  if "max_tokens" in body else httpx.Response(502, json={"detail": "limit"}))
    img = tmp_path / "p.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False).save(img)
    r = kit().ocr_document(str(img))
    assert r["text"] == "big table"
    assert any(b.get("max_tokens") == 8192 for p, b in api.requests if p == "/v1/chat/completions")


def test_ocr_rejects_other_file_types_and_budget(kit, api, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(BodhanError) as e:
        kit().ocr_document(str(f))
    assert e.value.code == "unsupported_file"
    pdf = tmp_path / "d.pdf"
    make_pdf(pdf, 4)
    with pytest.raises(BudgetExceeded):
        kit(budget=0.5).ocr_document(str(pdf))  # 4 pages x Rs 0.20 = 0.8
    assert api.requests == []


def test_inspect_pdf_reports_pages(kit, tmp_path):
    pdf = tmp_path / "d.pdf"
    make_pdf(pdf, 3)
    assert kit().inspect_file(str(pdf))["pages"] == 3


# ---- transcribe -------------------------------------------------------------------------
@needs_ffmpeg
def test_transcribe_long_audio_in_chunks(kit, api, tmp_path):
    f = tmp_path / "long.mp3"
    expr = "if(lt(mod(t\\,10)\\,9.2)\\,sin(2*PI*440*t)*0.5\\,0)"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=44100:d=70",
                    "-c:a", "libmp3lame", str(f)], check=True, capture_output=True)
    api.transcribe_fn = lambda n: f"भाग {n}"
    r = kit().transcribe_audio(str(f), "hi")
    segs = r["segments"]
    assert r["chunks"] == len(segs) >= 3 and api.count("/v1/audio/transcriptions") == r["chunks"]
    assert [s["start"] for s in segs] == sorted(s["start"] for s in segs) and segs[0]["start"] == 0
    assert r["duration_s"] == pytest.approx(70, abs=1) and r["detected"]["script"] == "Devanagari"


def test_transcribe_unsupported_language_and_missing_file(kit, api):
    with pytest.raises(BodhanError) as e:
        kit().transcribe_audio(__file__, "French")
    assert e.value.code == "unsupported_language"
    with pytest.raises(BodhanError) as e:
        kit().transcribe_audio("/no/such/file.wav")
    assert e.value.code == "file_not_found"
    assert api.requests == []


def test_usage_reports_cache_hits(kit, api):
    k = kit()
    k.translate_text("hi", text="hello")
    k.translate_text("hi", text="hello")
    u = k.get_usage()
    assert u["calls"] == 2 and u["cached_calls"] == 1 and u["total_inr"] >= 0
    


# ---- read_text ----------------------------------------------------------------------------
def test_read_text_slices_and_reports_whether_more_remains(kit, tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("abcdefghij" * 100, encoding="utf-8")  # 1000 chars
    k = kit()
    first = k.read_text(str(f), start=0, max_chars=300)
    assert first["text"] == "abcdefghij" * 30 and first["has_more"] is True and first["total_chars"] == 1000
    last = k.read_text(str(f), start=first["end"], max_chars=8000)
    assert last["end"] == 1000 and last["has_more"] is False


def test_read_text_only_reads_text_files(kit, tmp_path):
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(BodhanError) as e:
        kit().read_text(str(exe))
    assert e.value.code == "unsupported_file"