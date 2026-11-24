"""End-to-end smoke test on a two-page sample, with a fake Bodhan client (no network, no cost)."""
import json

from PIL import Image

from bodhan_lesson.client import CostTracker
from bodhan_lesson.pipeline import run_pipeline

H = 2000


class FakeBodhan:
    def __init__(self):
        self.costs = CostTracker()

    def ocr_page(self, image, max_tokens=None):
        self.costs.add("ocr", 1, 0.2, False, 0.0)
        if image.stem == "page_001":
            blocks = [
                {"order": 0, "label": "Chapter-title", "conf": 0.9, "bbox_xyxy": [100, 500, 600, 560], "text": "CHAPTER 6"},
                {"order": 1, "label": "Section-title", "conf": 0.9, "bbox_xyxy": [100, 700, 600, 760], "text": "6.1 Introduction"},
                {"order": 2, "label": "Paragraph", "conf": 0.95, "bbox_xyxy": [100, 800, 1200, 1000],
                 "text": "Angles on a line add up to $180^\\circ$. We study this because"},
            ]
        else:
            blocks = [
                {"order": 0, "label": "Paragraph", "conf": 0.95, "bbox_xyxy": [100, 300, 1200, 400],
                 "text": "triangles use the same rule, so the sum is 180 degrees."},
                {"order": 1, "label": "Equation", "conf": 0.9, "bbox_xyxy": [100, 450, 1200, 520], "text": "$$a + b + c = 180$$"},
            ]
        return {"markdown": "\n\n".join(b["text"] for b in blocks), "blocks": blocks}

    def translate(self, text, lang):
        self.costs.add("translate", 10, 0.001, False, 0.0)
        return {"text": text.replace("Angles", "Kon").replace("CHAPTER", "Adhyay"), "finish_reason": "stop", "truncated": False}


def make_pages(folder):
    folder.mkdir()
    for n in (1, 2):
        Image.new("RGB", (1000, H), "white").save(folder / f"page_{n}.png")


def test_two_page_end_to_end(tmp_path):
    make_pages(tmp_path / "scans")
    out = tmp_path / "out"
    report = run_pipeline(FakeBodhan(), tmp_path / "scans", ["hi", "ta"], out)

    assert report["ok"] and report["stages"]["ocr"]["failed_pages"] == []
    assert report["stages"]["assembly"]["merged_across_pages"] == [{"from_page": 1, "to_page": 2}]
    for lang in ("hi", "ta"):
        assert report["stages"]["translation"][lang]["structure"]["same_order_and_types"]
        assert (out / lang / f"lesson.{lang}.md").exists()
    source = (out / "source" / "lesson.src.md").read_text(encoding="utf-8")
    assert "because triangles use the same rule" in source and "$$a + b + c = 180$$" in source
    saved = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert saved["not_implemented"] and saved["stages"]["ocr"]["cost_inr_per_page"] == 0.2


def test_one_unreadable_page_does_not_stop_the_run(tmp_path):
    class Flaky(FakeBodhan):
        def ocr_page(self, image, max_tokens=None):
            if image.stem == "page_002":
                raise RuntimeError("503 service unavailable")
            return super().ocr_page(image, max_tokens)

    make_pages(tmp_path / "scans")
    report = run_pipeline(Flaky(), tmp_path / "scans", ["hi"], tmp_path / "out")
    assert report["stages"]["ocr"]["failed_pages"] == [2]
    assert report["ok"] is False and any("pages [2]" in p for p in report["problems"])
    assert (tmp_path / "out" / "hi" / "lesson.hi.md").exists()      # the readable page still got translated


def test_a_failing_language_does_not_stop_the_others(tmp_path):
    class BadTamil(FakeBodhan):
        def translate(self, text, lang):
            if lang == "ta":
                raise RuntimeError("429 budget reached")
            return super().translate(text, lang)

    make_pages(tmp_path / "scans")
    report = run_pipeline(BadTamil(), tmp_path / "scans", ["ta", "hi"], tmp_path / "out")
    assert "error" in report["stages"]["translation"]["ta"]
    assert report["stages"]["translation"]["hi"]["structure"]["same_order_and_types"]
    assert report["ok"] is False