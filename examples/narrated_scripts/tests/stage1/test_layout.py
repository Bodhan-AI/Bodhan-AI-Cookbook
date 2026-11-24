import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from backend import runner
from backend.models import OCRBlock, OCRPage
from backend.ocr.layout import order_blocks, validate_order
from bodhan_lesson import main


def forbidden(*args, **kwargs):
    pytest.fail("This input needs no LLM call")


def page_with(*blocks):
    ids = tuple(block.id for block in blocks)
    return OCRPage("p001", 1, "ok", blocks=blocks, raw_block_ids=ids, ordered_block_ids=ids)


def test_heading_bands_read_each_column_before_the_next():
    page = page_with(
        OCRBlock("heading", "heading", "Angles", bbox=(0, 0, 100, 10)),
        OCRBlock("left-1", "paragraph", "First", bbox=(0, 15, 45, 28)),
        OCRBlock("right-1", "paragraph", "Third", bbox=(55, 15, 100, 28)),
        OCRBlock("left-2", "paragraph", "Second", bbox=(0, 32, 45, 45)),
        OCRBlock("right-2", "paragraph", "Fourth", bbox=(55, 32, 100, 45)),
        OCRBlock("next-heading", "heading", "Exercises", bbox=(0, 50, 100, 60)),
        OCRBlock("exercise", "paragraph", "Fifth", bbox=(0, 65, 100, 75)),
    )
    order = order_blocks(page, forbidden)
    assert order.method == "geometry"
    assert order.ids == (
        "heading", "left-1", "left-2", "right-1", "right-2", "next-heading", "exercise",
    )


@pytest.mark.parametrize("proposed,accepted", [
    (["sidebar", "body"], True),
    (["sidebar", "sidebar"], False),
    (["sidebar"], False),
    (["sidebar", "foreign"], False),
    (["sidebar", {}], False),
])
def test_ambiguous_sidebar_requires_exact_permutation(proposed, accepted):
    page = page_with(
        OCRBlock("body", "paragraph", "Main explanation", bbox=(0, 2, 75, 70)),
        OCRBlock("sidebar", "sidebar", "Try this", bbox=(65, 10, 100, 30)),
    )

    def propose(page_id, blocks):
        assert page_id == page.page_id
        assert [(block.id, block.kind) for block in blocks] == [
            ("body", "paragraph"), ("sidebar", "sidebar"),
        ]
        return json.dumps({"ordered_block_ids": proposed})

    assert validate_order(proposed, page.ordered_block_ids) == accepted
    order = order_blocks(page, propose)
    assert order.fallback is not accepted
    assert order.ids == (tuple(proposed) if accepted else page.ordered_block_ids)


def test_missing_geometry_keeps_provider_order_and_flags_fallback():
    page = page_with(
        OCRBlock("first", "paragraph", "First"),
        OCRBlock("second", "paragraph", "Second", bbox=(0, 1, 10, 10)),
    )
    result = order_blocks(page, forbidden)
    assert result.fallback and result.ids == page.ordered_block_ids
    assert "no geometry" in result.reason


def test_checkpoint_keeps_ids_and_evidence_for_repaired_and_quarantined_blocks(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    png_path = tmp_path / "page.png"
    # The original is landscape with EXIF requesting a clockwise quarter turn.
    with Image.new("RGB", (200, 140), "white") as image:
        exif = image.getexif()
        exif[274] = 6
        image.save(png_path, exif=exif)

    boxes = [(10, 15, 110, 42), (10, 55, 110, 86), (10, 100, 110, 120)]

    def raw_block(label, text, confidence, order=0):
        return {"label": label, "type": "Text", "text": text, "conf": confidence,
                "order": order, "bbox_xyxy": boxes[order]}

    calls = []

    def recognize(self, png, options):
        with Image.open(BytesIO(png)) as image:
            calls.append(image.size)
        if len(calls) == 1:
            blocks = [
                raw_block("Paragraph", "Parallel llnes meet here.", 0.60),
                raw_block("Paragraph", "An ang\uFFFDle", 0.90, 1),
                raw_block("Handwritten", "Margin note", 0.92, 2),
            ]
        elif len(calls) == 2:
            blocks = [raw_block("Paragraph", "Parallel lines meet here.", 0.95)]
        else:
            blocks = [raw_block("Table", "<table><tr><td>1</td></tr></table>", 0.98)]
        return {"markdown": "", "blocks": blocks}

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner, "propose_reading_order", forbidden)
    out = tmp_path / "out"
    result = main(["--input", str(tmp_path), "--out", str(out), "--langs", "hi",
                   "--source-lang", "en", "--until", "layout"])
    assert result == 0
    assert calls == [(140, 200), (104, 31), (104, 35)]
    canonical = json.loads((out / "source/pages/p001.json").read_text(encoding="utf-8"))
    [decision] = json.loads((out / "work/layout.json").read_text(encoding="utf-8"))["pages"]
    assert canonical["status"] == "partial"
    assert decision["before_ids"] == decision["after_ids"] == canonical["ordered_block_ids"]
    repaired, rejected, handwriting = canonical["blocks"]
    assert repaired["id"] == "p001-b001" and repaired["text"] == "Parallel lines meet here."
    assert repaired["flags"] == ["ocr_repaired"]
    assert "quarantined" in rejected["flags"] and rejected["text"] == "An ang\uFFFDle"
    assert {"handwriting", "quarantined"} <= set(handwriting["flags"])
    assert decision["quarantined_ids"] == [rejected["id"], handwriting["id"]]
    first, second, third = decision["repairs"]
    assert first["original_text"] == canonical["raw_response"]["blocks"][0]["text"]
    assert first["replacement_text"] == repaired["text"]
    assert first["original_crop_box"] == [13, 28, 44, 132]
    assert first["disposition"] == "accepted" and first["attempt_ids"] == ["a000002"]
    assert canonical["attempts"][1]["entity_ids"] == [repaired["id"]]
    assert second["disposition"] == "rejected" and second["attempt_ids"] == ["a000003"]
    assert second["crop_candidates"][0]["kind"] == "table"
    assert third["disposition"] == "skipped" and third["attempt_ids"] == []


@pytest.mark.parametrize("crop_tail,complete", [("6 degrees.", True), ("8 degrees.", False)])
def test_low_layout_confidence_accepts_exact_crop_text_in_order(
    tmp_path, monkeypatch, crop_tail, complete,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    with Image.new("RGB", (200, 140), "white") as image:
        image.save(tmp_path / "page.png")

    text = "Figure 1. The difference is 6 degrees."
    responses = iter((
        {"markdown": "", "blocks": [
            {"label": "Figure", "text": "", "order": 0, "bbox_xyxy": [10, 10, 160, 55]},
            {"label": "Image-caption", "text": text, "conf": 0.60, "order": 1,
             "bbox_xyxy": [10, 65, 160, 103]},
        ]},
        {"markdown": "", "blocks": [
            {"label": "Paragraph", "text": crop_tail, "conf": 0.62, "order": 1},
            {"label": "Paragraph", "text": "Figure 1. The difference is", "conf": 0.61, "order": 0},
        ]},
    ))

    def recognize(self, png, options):
        return next(responses)

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner, "propose_reading_order", forbidden)
    out = tmp_path / "out"
    assert main(["--input", str(tmp_path), "--out", str(out), "--langs", "hi",
                 "--source-lang", "en", "--until", "source"]) == 0
    [record] = json.loads((out / "work/layout.json").read_text())["pages"]
    [repair] = record["repairs"]
    assert repair["disposition"] == ("accepted" if complete else "rejected")
    assert [candidate["text"] for candidate in repair["crop_candidates"]] == [
        "Figure 1. The difference is", crop_tail,
    ]
    assert record["status"] == ("ok" if complete else "partial")
    page = json.loads((out / "source/pages/p001.json").read_text())
    figure, caption = page["blocks"]
    assert caption["caption_for"] == figure["id"]
    assert caption["text"] == text and caption["confidence"] == 0.60
    assert ("ocr_corroborated" in caption["flags"]) is complete
    markdown = (out / "source/lesson.src.md").read_text()
    assert (text in markdown) is complete
    assert ("[Unavailable block p001-b002]" in markdown) is not complete


def test_layout_llm_sends_only_geometry_and_existing_ids(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "hosted")
    monkeypatch.setenv("BODHAN_OCR_KEY", "fake-ocr")
    monkeypatch.setenv("LLM_BASE_URL", "https://layout.test/v1")
    monkeypatch.setenv("LLM_MODEL", "fake-layout")
    monkeypatch.setenv("LLM_API_KEY", "fake-llm")
    with Image.new("RGB", (120, 160), "white") as image:
        image.save(tmp_path / "page.png")

    def handler(request):
        payload = json.loads(request.content)
        if request.url.host == "layout.test":
            evidence = json.loads(payload["messages"][-1]["content"])
            assert all("text" not in block for block in evidence)
            result = json.dumps({"ordered_block_ids": ["p001-b002", "p001-b001"]})
            return httpx.Response(200, json={"choices": [{"message": {"content": result}}]})
        assert request.url.host == "api.bodhan.ai"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Main text and sidebar."}}],
            "blocks": [
                {"order": 0, "label": "Paragraph", "text": "Main text.",
                 "bbox_xyxy": [0, 2, 75, 70], "conf": 0.99},
                {"order": 1, "label": "Infobox", "text": "Try it.",
                 "bbox_xyxy": [65, 10, 100, 30], "conf": 0.99},
            ],
        })

    create_client = httpx.Client
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: create_client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    out = tmp_path / "out"
    assert main(["--input", str(tmp_path), "--out", str(out), "--langs", "hi",
                 "--source-lang", "en", "--until", "layout"]) == 0
    [decision] = json.loads((out / "work/layout.json").read_text(encoding="utf-8"))["pages"]
    assert decision["method"] == "llm" and decision["order_attempt_ids"] == ["a000002"]
    assert decision["after_ids"] == ["p001-b002", "p001-b001"]
