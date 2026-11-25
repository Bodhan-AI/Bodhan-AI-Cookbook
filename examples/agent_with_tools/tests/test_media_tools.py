"""Offline tests (httpx.MockTransport) for transcribe, speak, ocr and language detection. No live calls."""

import io
import wave

import httpx
import pypdfium2 as pdfium
import pytest

from bodhan_core.config import load
from bodhan_core.langid import detect_language
from bodhan_mcp.tools import ocr as ocr_mod
from bodhan_mcp.tools import speak as speak_mod
from bodhan_mcp.tools import transcribe as transcribe_mod


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")
    monkeypatch.setenv("BODHAN_BASE_URL", "https://example.test")
    for m in (ocr_mod, speak_mod, transcribe_mod):
        m._CACHE.clear()


def wav_bytes(seconds: float, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def test_config_holds_limits():
    cfg = load()
    assert cfg["transcribe"]["window_s"] <= 30 and cfg["ocr"]["max_pages"] > 0


def test_detect_language():
    assert detect_language("पनडुब्बी के अंदर").language == "hi"
    assert detect_language("मी आळशी आहे").language == "mr"
    assert detect_language("வணக்கம்").language == "ta"
    assert detect_language("hello world").language == "en"
    assert detect_language("1234 !!") is None


async def test_transcribe_windows_long_audio(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(wav_bytes(60))  # 60 s -> 3 windows of 25/25/10 s
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/audio/transcriptions"
        seen.append(req)
        return httpx.Response(200, json={"text": f"part{len(seen)}"})

    res = await transcribe_mod.transcribe(str(audio), "hi", transport=httpx.MockTransport(handler))
    assert res.ok and res.text == "part1 part2 part3" and len(seen) == 3
    assert res.cost_inr == pytest.approx(60 / 60 * 0.10)
    body = seen[0].content
    assert b'name="model"' in body and b"indic-transcribe" in body and b'name="language"' in body


async def test_transcribe_errors(tmp_path):
    assert (await transcribe_mod.transcribe(str(tmp_path / "missing.wav"))).error.code == "file_not_found"
    audio = tmp_path / "a.wav"
    audio.write_bytes(wav_bytes(1))
    assert (await transcribe_mod.transcribe(str(audio), "fr")).error.code == "unsupported_language"


async def test_speak_splits_and_joins(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        calls.append(body)
        return httpx.Response(200, content=wav_bytes(1, 24000))

    text = "यह पहला वाक्य है। " * 30  # well above the 200-char request size
    out = tmp_path / "x" / "out.wav"
    res = await speak_mod.speak(text, None, str(out), transport=httpx.MockTransport(handler))
    assert res.ok and res.language == "hi" and res.voice == "Kavya" and "AI-generated" in res.notice
    assert len(calls) == res.chunks > 1 or len(calls) < res.chunks  # identical sentences hit the cache
    assert all(len(c["input"]) <= 200 and c["model"] == "indic-speak" for c in calls)
    assert calls[0]["instructions"] == '{"lang": "hi"}'
    assert out.exists()


async def test_speak_english_uses_listed_voice(tmp_path):
    def handler(req):
        return httpx.Response(200, content=wav_bytes(1, 24000))

    res = await speak_mod.speak("The meeting has been postponed.", None, str(tmp_path / "o.wav"), gender="male", transport=httpx.MockTransport(handler))
    assert res.ok and res.voice == "Amit" and res.language == "en"


def _pdf(path, pages: int):
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(200, 200)
    pdf.save(str(path))


async def test_ocr_pdf_reads_each_page(tmp_path):
    pdf = tmp_path / "d.pdf"
    _pdf(pdf, 3)
    n = []

    def handler(req: httpx.Request) -> httpx.Response:
        n.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": f"text{len(n)}"}}]})

    res = await ocr_mod.ocr_document(str(pdf), transport=httpx.MockTransport(handler))
    assert res.ok and res.page_count == 3 and [p.page for p in res.pages] == [1, 2, 3]
    assert "<!-- page 2 -->" in res.markdown
    assert res.cost_inr == pytest.approx(0.20 * len(n))


async def test_ocr_pdf_page_range_and_limit(tmp_path, monkeypatch):
    pdf = tmp_path / "d.pdf"
    _pdf(pdf, 3)

    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "t"}}]})

    t = httpx.MockTransport(handler)
    res = await ocr_mod.ocr_document(str(pdf), 2, 3, transport=t)
    assert res.ok and [p.page for p in res.pages] == [2, 3] and res.page_count == 3
    assert (await ocr_mod.ocr_document(str(pdf), 5, transport=t)).error.code == "bad_page_range"
    monkeypatch.setattr(ocr_mod, "MAX_PAGES", 2)
    assert (await ocr_mod.ocr_document(str(pdf), transport=t)).error.code == "too_many_pages"


async def test_ocr_image_request_shape(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG fake")

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        assert body["model"] == "indic-ocr"
        assert body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    res = await ocr_mod.ocr_document(str(img), transport=httpx.MockTransport(handler))
    assert res.ok and res.markdown == "hello" and res.cost_inr == 0.20
    assert (await ocr_mod.ocr_document(str(tmp_path / "x.txt"))).error.code in ("file_not_found",)
