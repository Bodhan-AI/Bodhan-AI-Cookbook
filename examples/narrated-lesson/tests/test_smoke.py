"""End-to-end smoke test on a two-page sample: PDF -> OCR -> lesson -> narration -> translate ->
speak -> back-check -> MP3. Every Bodhan call is served by a mock transport (no network, no cost)."""

import json

import httpx
import pymupdf

from bodhan_lesson import config, storage
from bodhan_lesson.clients.bodhan import BodhanClient
from bodhan_lesson.stages import ingest, localize, ocr
from bodhan_lesson.stages.assemble import assemble
from bodhan_lesson.stages.cleanup import cleanup_pages
from bodhan_lesson.stages.figures import describe_figures
from bodhan_lesson.stages.narrate import narrate
from bodhan_lesson.audio import wav

PAGE_BLOCKS = [
    [{"order": 0, "label": "Section-header", "type": "SectionHeader", "bbox_xyxy": [100, 100, 700, 150], "conf": 0.98,
      "text": "6.1 Introduction"},
     {"order": 1, "label": "Paragraph", "type": "Text", "bbox_xyxy": [100, 200, 700, 400], "conf": 0.95,
      "text": "A line has no end points. A ray starts at a point and goes on forever in one direction."},
     {"order": 2, "label": "Page-footer", "type": "PageFooter", "bbox_xyxy": [100, 1500, 700, 1550], "conf": 0.9,
      "text": "www.example.com"}],
    [{"order": 0, "label": "Section-header", "type": "SectionHeader", "bbox_xyxy": [100, 100, 700, 150], "conf": 0.98,
      "text": "6.2 Angles"},
     {"order": 1, "label": "Paragraph", "type": "Text", "bbox_xyxy": [100, 200, 700, 400], "conf": 0.95,
      "text": "Two rays with a common end point form an angle. A right angle measures 90 degrees."}],
]


def make_pdf(path):
    doc = pymupdf.open()
    for _ in PAGE_BLOCKS:
        doc.new_page(width=600, height=800)
    doc.save(path)


def test_two_page_sample_runs_end_to_end(tmp_path):
    pdf, out = tmp_path / "sample.pdf", tmp_path / "out"
    make_pdf(pdf)
    calls = iter(PAGE_BLOCKS)

    def ocr_handler(request):
        blocks = next(calls)
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}], "blocks": blocks})

    ocr_client = ocr.IndicOcrClient("k", client=httpx.Client(transport=httpx.MockTransport(ocr_handler)))
    images = ingest.ingest(pdf, out / "pages_png", dpi=72, max_pages=None)
    assert len(images) == 2
    source = out / "source"
    pages = ocr.run_ocr(ocr_client, sorted(images, key=lambda i: i.number)[:1], source / "pages")  # page 1
    pages += ocr.run_ocr(ocr_client, images[1:], source / "pages")
    summary = cleanup_pages(pages, figure_dir=source / "figures")
    assert summary  # footers dropped, headings kept
    lesson = assemble(pages)
    assert [s.title for s in lesson.sections] and not any("example.com" in e.markdown for e in lesson.elements)
    storage.save_lesson(lesson, source)
    figures = describe_figures(lesson, source, None)
    sections, stats = narrate(lesson, figures, None)
    storage.save_narration(sections, stats, source)

    def bodhan_handler(request):
        path = request.url.path
        if path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "नमस्ते कक्षा। यह एक पाठ है।"},
                                                           "finish_reason": "stop"}]})
        if path.endswith("/audio/speech"):
            return httpx.Response(200, content=wav.join([b"\x01\x01" * 24000 * 3], 24000))
        return httpx.Response(200, json={"text": "नमस्ते कक्षा यह एक पाठ है"})

    client = BodhanClient(config.BodhanKeys("t", "s", "x"), cache_dir=tmp_path / "cache",
                          http=httpx.Client(transport=httpx.MockTransport(bodhan_handler)), sleep=lambda s: None)
    report = localize.localize(out, ["hi", "ta"], client)
    for lang in ("hi", "ta"):
        r = report["languages"][lang]
        assert r["units_failed"] == 0 and r["chunks_failed"] == 0 and r["cer"] is not None
        assert (out / lang / f"narration_script.{lang}.json").exists()
        assert (out / lang / f"lesson.{lang}.mp3").exists() or (out / lang / f"lesson.{lang}.wav").exists()
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["wall_time_s"] >= 0
