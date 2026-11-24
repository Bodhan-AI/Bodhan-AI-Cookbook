import json
from io import BytesIO

import httpx
import pypdfium2 as pdfium
import pytest
from PIL import Image

from backend import runner
from backend.inference import Inference, ProviderError
from backend.llm import classify_language
from backend.ocr.inputs import discover_pages
from backend.ocr.language import PageEvidence, resolve_language, sample_language
from backend.ocr.provider import IndicOCRProvider, OCROptions, OCRResponse
from backend.report import AttemptLog
from bodhan_lesson import main

ENGLISH = "Angles on a straight line form a sum of one hundred and eighty degrees."
HINDI = "एक सरल रेखा पर बने दो आसन्न कोणों का योग एक सौ अस्सी अंश होता है।"
TAMIL = "ஒரு நேர்கோட்டில் உள்ள கோணங்களின் கூட்டுத்தொகை நூற்று எண்பது பாகை."


def forbidden(*args, **kwargs):
    pytest.fail("This boundary must not be called")


def test_cli_override_skips_models(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.LocalOCR, "__call__", forbidden)
    monkeypatch.setattr(runner.IndicOCRProvider, "ocr", forbidden)
    monkeypatch.setattr(runner, "classify_language", forbidden)
    (tmp_path / "page.png").touch()
    out = tmp_path / "out"
    assert main(["--input", str(tmp_path), "--langs", "hi", "--out", str(out),
                 "--source-lang", "en", "--until", "language"]) == 0
    result = json.loads((out / "source" / "language.json").read_text(encoding="utf-8"))
    assert (result["code"], result["method"]) == ("en", "override")
    assert result["sample_page_ids"] == result["attempts"] == []


def test_automatic_checkpoint_persists_ocr_and_llm_attempts(tmp_path, monkeypatch):
    Image.new("RGB", (8, 8), "white").save(tmp_path / "page.png")
    settings = {
        "BODHAN_BACKEND": "hosted",
        "BODHAN_OCR_KEY": "fake-ocr-key",
        "LLM_BASE_URL": "https://classifier.test/v1",
        "LLM_MODEL": "file-default",
        "LLM_API_KEY": "fake-llm-key",
    }
    monkeypatch.chdir(tmp_path)
    for name in settings:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODEL", "fake-classifier")
    (tmp_path / ".env").write_text(
        "".join(f"{name}={value}\n" for name, value in settings.items()), encoding="utf-8"
    )

    def handler(request):
        body = json.loads(request.content)
        if request.url.host == "api.bodhan.ai":
            assert body["model"] == "indic-ocr"
            assert request.headers["Authorization"] == "Bearer fake-ocr-key"
            return httpx.Response(200, json={
                "choices": [{"message": {"content": HINDI}}], "blocks": [],
            })
        assert request.url.host == "classifier.test"
        assert body["model"] == "fake-classifier"
        assert request.headers["Authorization"] == "Bearer fake-llm-key"
        assert HINDI in body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    create_client = httpx.Client

    def mock_client(**kwargs):
        return create_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(runner.httpx, "Client", mock_client)
    monkeypatch.setattr(runner.LocalOCR, "__call__", forbidden)
    out = tmp_path / "out"
    assert main(["--input", str(tmp_path), "--langs", "ta", "--out", str(out),
                 "--until", "language"]) == 0
    serialized = (out / "source" / "language.json").read_text(encoding="utf-8")
    result = json.loads(serialized)
    assert (result["code"], result["method"]) == ("hi", "llm")
    assert result["sample_page_ids"] == ["p001"]
    assert [attempt["service"] for attempt in result["attempts"]] == ["indic-ocr", "llm"]
    assert [attempt["id"] for attempt in result["attempts"]] == ["a000001", "a000002"]
    assert "fake-ocr-key" not in serialized and "fake-llm-key" not in serialized


def test_invalid_override_is_an_argument_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as failure:
        main(["--input", str(tmp_path), "--langs", "hi", "--out", str(tmp_path),
              "--source-lang", "xx", "--until", "language"])
    assert failure.value.code == 2
    assert "Unsupported --source-lang 'xx'" in capsys.readouterr().err


def test_natural_image_order_and_ties(tmp_path):
    for name in ["page-10.png", "page-2.jpg", "page-2.JPG", "page-02.JPEG", "notes.txt"]:
        (tmp_path / name).touch()
    assert [(page.page_id, page.path.name) for page in discover_pages(tmp_path)] == [
        ("p001", "page-02.JPEG"), ("p002", "page-2.JPG"),
        ("p003", "page-2.jpg"), ("p004", "page-10.png"),
    ]


def test_invalid_inputs_are_actionable(tmp_path):
    with pytest.raises(ValueError, match="no PNG or JPEG"):
        discover_pages(tmp_path)
    with pytest.raises(ValueError, match="existing PNG/JPEG folder or PDF"):
        discover_pages(tmp_path / "missing.pdf")


def test_blank_then_readable_samples_both_pages(tmp_path):
    for index in (1, 2):
        Image.new("RGB", (8, 8), "white").save(tmp_path / f"page-{index}.png")

    def ocr(page_id, image):
        assert image.startswith(b"\x89PNG")
        return OCRResponse("" if page_id == "p001" else ENGLISH, {}, "", ())

    def classify(candidates, samples):
        assert candidates == ("en",)
        assert samples == (("p002", ENGLISH),)
        return "en"

    result = sample_language(discover_pages(tmp_path), ocr, classify, AttemptLog())
    assert (result.code, result.method) == ("en", "llm")
    assert result.sample_page_ids == ("p001", "p002")
    assert [page.status for page in result.samples] == ["blank", "readable"]


def test_sampling_caps_readable_pages_and_continues_after_failure(tmp_path):
    for index in range(1, 6):
        Image.new("RGB", (8, 8), "white").save(tmp_path / f"page-{index}.png")

    def ocr(page_id, image):
        if page_id == "p001":
            raise ProviderError("timeout", "Sample timed out.")
        return OCRResponse(TAMIL, {}, "", ())

    result = sample_language(discover_pages(tmp_path), ocr, forbidden, AttemptLog())
    assert (result.code, result.method) == ("ta", "script")
    assert result.sample_page_ids == ("p001", "p002", "p003", "p004")
    assert result.samples[0].reason == "Sample timed out."


@pytest.mark.parametrize("text,label", [(ENGLISH, "fr"), (ENGLISH, "hi"), (HINDI, "unknown")])
def test_unresolved_labels_are_rejected(text, label):
    result = resolve_language(None, (PageEvidence("p001", text),), lambda *_: label)
    assert result.code is None
    assert result.method == "unresolved"
    assert result.reason


@pytest.mark.parametrize("text", [ENGLISH, HINDI])
def test_script_alone_cannot_resolve_english_or_hindi(text):
    result = resolve_language(None, (PageEvidence("p001", text),))
    assert result.code is None
    assert "requires a language classifier" in result.reason


def test_devanagari_candidates_include_hindi_and_marathi():
    def classify(candidates, samples):
        assert {"hi", "mr"} <= set(candidates)
        assert samples == (("p001", HINDI),)
        return "hi"

    result = resolve_language(None, (PageEvidence("p001", HINDI),), classify)
    assert (result.code, result.method) == ("hi", "llm")


def write_pdf_with_invisible_text(path):
    # Rendering mode 3 keeps this text searchable while leaving the page visually blank.
    content = b"BT /F1 12 Tf 3 Tr 15 40 Td (Misleading English text layer) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(data)


def test_pdf_transcripts_come_from_rendered_pixels_and_ocr(tmp_path, monkeypatch):
    path = tmp_path / "page.pdf"
    write_pdf_with_invisible_text(path)
    events = []
    original_render = pdfium.PdfPage.render

    def render(page, **kwargs):
        events.append("render")
        return original_render(page, **kwargs)

    monkeypatch.setattr(pdfium.PdfPage, "render", render)
    monkeypatch.setattr(pdfium.PdfPage, "get_textpage", forbidden)

    def ocr(page_id, image):
        events.append("ocr")
        assert page_id == "p001"
        with Image.open(BytesIO(image)) as pixels:
            assert pixels.format == "PNG"
            assert pixels.getextrema() == ((255, 255),) * 3
        return OCRResponse(TAMIL, {}, "", ())

    pages = discover_pages(path)
    assert pages[0].pdf_offset == 0
    assert events == []
    result = sample_language(pages, ocr, forbidden, AttemptLog())
    assert result.code == "ta"
    assert events == ["render", "ocr"]


def hosted_ocr(request):
    body = json.loads(request.content)
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer fake-key"
    assert body["model"] == "indic-ocr"
    assert body["messages"] == [{
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}}],
    }]
    assert body["table_format"] == "html"
    return httpx.Response(200, json={"choices": [{"message": {"content": ENGLISH}}], "blocks": []})


@pytest.mark.parametrize("mode", ["local-first", "hosted"])
def test_backend_success_uses_only_selected_path(mode):
    attempts = AttemptLog()
    handler = hosted_ocr if mode == "hosted" else forbidden
    local = forbidden if mode == "hosted" else lambda *_: {"markdown": ENGLISH}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = IndicOCRProvider(mode, Inference(attempts, forbidden), client,
                                    {"BODHAN_OCR_KEY": "fake-key"}, local, forbidden)
        result = provider.ocr("p001", b"png")
    assert result.markdown == ENGLISH
    assert result.attempt_ids == ("a000001",)
    [attempt] = attempts.attempts
    assert attempt.backend == ("hosted" if mode == "hosted" else "local")
    assert attempt.usage.cost_inr == (0.20 if mode == "hosted" else 0)


@pytest.mark.parametrize("failure_phase", ["load", "inference"])
def test_cuda_oom_falls_back_for_one_request(failure_phase):
    attempts = AttemptLog()
    events = []
    oom_type = type("OutOfMemoryError", (RuntimeError,), {"__module__": "torch"})

    def local(image, options):
        events.append("load")
        if image == b"png" and failure_phase == "load":
            raise oom_type("GPU details that must stay private")
        events.append("inference")
        if image == b"png":
            raise RuntimeError("CUDA error: out of memory")
        return {"markdown": ENGLISH}

    def hosted(request):
        events.append("hosted")
        return hosted_ocr(request)

    with httpx.Client(transport=httpx.MockTransport(hosted)) as client:
        provider = IndicOCRProvider("local-first", Inference(attempts, forbidden), client,
                                    {"BODHAN_OCR_KEY": "fake-key"}, local,
                                    lambda: events.append("release"))
        first = provider.ocr("p001", b"png")
        assert events[-2:] == ["release", "hosted"]
        provider.ocr("p002", b"next")
    assert first.attempt_ids == ("a000001", "a000002")
    assert [attempt.backend for attempt in attempts.attempts] == ["local", "hosted", "local"]
    failed, fallback, _ = attempts.attempts
    assert failed.error_category == "cuda_oom"
    assert failed.reason == fallback.fallback_reason == "Local CUDA out of memory"
    assert fallback.usage.cost_inr == 0.20


@pytest.mark.parametrize("message", ["CPU out of memory", "CUDA unavailable", "Missing weights"])
def test_non_cuda_oom_failures_never_call_hosted(message):
    attempts = AttemptLog()

    def local(*args):
        raise RuntimeError(message)

    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        provider = IndicOCRProvider("local-first", Inference(attempts, forbidden), client,
                                    {"BODHAN_OCR_KEY": "fake-key"}, local, forbidden)
        with pytest.raises(ProviderError, match="Check the model installation"):
            provider.ocr("p001", b"png")
    assert [attempt.backend for attempt in attempts.attempts] == ["local"]


def test_missing_key_after_cuda_oom_keeps_both_attempt_records():
    attempts = AttemptLog()

    def local(*args):
        raise RuntimeError("CUDA out of memory")

    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        provider = IndicOCRProvider("local-first", Inference(attempts, forbidden), client,
                                    {}, local, lambda: None)
        with pytest.raises(ProviderError, match="Set BODHAN_OCR_KEY"):
            provider.ocr("p001", b"png")
    assert [attempt.error_category for attempt in attempts.attempts] == [
        "cuda_oom", "configuration",
    ]
    assert attempts.attempts[1].usage.cost_inr == 0


def test_hosted_retry_records_and_respects_retry_after():
    statuses = iter([429, 503, 200])
    waits = []
    attempts = AttemptLog()

    def handler(request):
        status = next(statuses)
        if status == 200:
            return hosted_ocr(request)
        return httpx.Response(status, headers={"Retry-After": "4"},
                              text="fake-key must not appear in artifacts")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = IndicOCRProvider("hosted", Inference(attempts, waits.append), client,
                                    {"BODHAN_OCR_KEY": "fake-key"}, forbidden, forbidden)
        response = provider.ocr("p001", b"png")
    assert waits == [4, 4]
    assert len(response.attempt_ids) == 3
    assert [attempt.error_category for attempt in attempts.attempts] == [
        "rate_limit", "transient_http", None,
    ]
    assert "fake-key" not in repr(attempts.attempts)


def test_rate_limited_page_does_not_block_later_page_and_honors_long_retry_after():
    statuses = iter([429, 429, 429, 200])
    waits = []
    attempts = AttemptLog()

    def handler(request):
        status = next(statuses)
        if status == 200:
            return hosted_ocr(request)
        return httpx.Response(status, headers={"Retry-After": "45"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = IndicOCRProvider("hosted", Inference(attempts, waits.append), client,
                                    {"BODHAN_OCR_KEY": "fake-key"}, forbidden, forbidden)
        with pytest.raises(ProviderError, match="HTTP 429"):
            provider.ocr("p001", b"png")
        later = provider.ocr("p002", b"png")

    assert later.markdown == ENGLISH
    assert waits == [45, 45, 45]
    assert [attempt.number for attempt in attempts.attempts] == [1, 2, 3, 1]
    assert [attempt.error_category for attempt in attempts.attempts] == [
        "rate_limit", "rate_limit", "rate_limit", None,
    ]


def test_cache_reuses_only_same_page_image_and_options():
    attempts = AttemptLog()

    def local(image, options):
        return {"markdown": ENGLISH}

    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        provider = IndicOCRProvider("local-first", Inference(attempts, forbidden), client,
                                    {}, local, forbidden)
        first = provider.ocr("p001", b"png")
        assert provider.ocr("p001", b"png") is first
        assert provider.ocr("p001", b"new").image_digest != first.image_digest
        assert provider.ocr("p001", b"png", OCROptions("markdown")).attempt_ids != first.attempt_ids
        assert provider.ocr("p002", b"png").attempt_ids != first.attempt_ids
    assert len(attempts.attempts) == 4


def test_llm_sends_only_label_options_and_ocr_excerpts():
    attempts = AttemptLog()

    def handler(request):
        assert request.url.path == "/v2/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "fake-classifier"
        system, user = body["messages"]
        assert "hi (Hindi), mr (Marathi), or unknown" in system["content"]
        assert json.loads(user["content"]) == [{"page_id": "p001", "text": HINDI}]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 57},
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        label = classify_language(client, Inference(attempts, forbidden), {
            "LLM_BASE_URL": "https://example.test/v2/",
            "LLM_MODEL": "fake-classifier",
            "LLM_API_KEY": "fake-llm-key",
        }, ("hi", "mr"), (("p001", HINDI),))
    assert label == "hi"
    assert attempts.attempts[0].usage.quantity == 57
    assert attempts.attempts[0].usage.cost_inr is None
