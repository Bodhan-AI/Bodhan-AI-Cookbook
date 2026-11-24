from pathlib import Path

from PIL import Image

from bodhan_lesson.assemble import assemble
from bodhan_lesson.ocr_stage import block_flags, ocr_one


class FakeClient:
    """Raises the recognizer's truncation error for any image the rule selects."""

    def __init__(self, fails):
        self.fails = fails
        self.calls = []

    def ocr_page(self, image, max_tokens=None):
        h = Image.open(image).size[1]
        self.calls.append((Path(image).stem, max_tokens))
        if self.fails(Path(image).stem, h):
            raise RuntimeError("502 502: the recognizer did not return a usable transcription (order 11: truncated)")
        return {"markdown": f"text of {Path(image).stem}", "blocks": [
            {"order": 0, "label": "Paragraph", "conf": 0.95, "bbox_xyxy": [10, 10, 300, 60], "text": f"text of {Path(image).stem}"}]}


def make_page(tmp_path, h=2000):
    p = tmp_path / "page_008.jpg"
    Image.new("RGB", (800, h), "white").save(p)
    return p


def test_tall_failures_are_solved_by_splitting(tmp_path):
    img = make_page(tmp_path)
    client = FakeClient(lambda stem, h: h > 1000)
    rec = ocr_one(client, 8, img)
    assert rec["status"] == "ok" and not any(b["label"] == "Failed-region" for b in rec["blocks"])
    assert any(n.startswith("split:") for n in rec["notes"])
    ys = [b["bbox_xyxy"][1] for b in rec["blocks"]]
    assert ys == sorted(ys)                       # blocks come back in top-to-bottom order


def test_a_region_that_keeps_failing_becomes_a_flagged_gap_not_a_failed_page(tmp_path):
    img = make_page(tmp_path)
    client = FakeClient(lambda stem, h: stem in {"page_008", "page_008_bottom"} or stem.endswith("bottom_bottom"))
    rec = ocr_one(client, 8, img)
    assert rec["status"] == "ok"
    gaps = [b for b in rec["blocks"] if b["label"] == "Failed-region"]
    assert len(gaps) == 1 and gaps[0]["flags"] == ["ocr_failed_region"]
    assert gaps[0]["bbox_xyxy"][1] > 1000          # the failed region sits in the lower half
    elements, _, md, report = assemble([rec])
    assert any(e["type"] == "gap" for e in elements)
    assert "ocr_failed_region" in md and report["flagged"]


def test_other_errors_are_not_hidden_by_splitting(tmp_path):
    img = make_page(tmp_path)

    class Down:
        def ocr_page(self, image, max_tokens=None):
            raise RuntimeError("503 service unavailable")

    rec = ocr_one(Down(), 8, img)
    assert rec["status"] == "failed" and "503" in rec["error"]


def test_block_flags_ignore_spacing_and_diagrams_and_math_confidence():
    assert block_flags({"label": "Diagram", "text": "", "conf": 0.2}) == []
    assert block_flags({"label": "Paragraph", "text": "Why?        (1)", "conf": 0.9}) == []
    assert block_flags({"label": "Solved-example", "text": "x = $5$ and more words here", "conf": 0.55}) == []
    assert block_flags({"label": "Paragraph", "text": "plain words here", "conf": 0.55}) == ["low_confidence:0.55"]
    assert "repeated_character_run" in block_flags({"label": "Paragraph", "text": "aaaaaaaa text", "conf": 0.9})