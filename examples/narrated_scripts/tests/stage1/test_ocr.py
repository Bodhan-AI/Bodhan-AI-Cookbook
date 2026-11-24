import json
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from PIL import Image

from backend import runner
from backend.inference import Inference
from backend.models import Page
from backend.ocr.extraction import extract_pages, ocr_page, parse_ocr_response
from backend.ocr.inputs import discover_pages
from backend.ocr.preprocess import PreprocessSettings, preprocess_pages
from backend.ocr.provider import IndicOCRProvider, OCRResponse
from backend.report import AttemptLog
from bodhan_lesson import main

FORMULA = r"\angle ABC = 90^\circ"
TABLE = (
    '<table><tr><th rowspan="2">Region</th><th colspan="2">Angles</th></tr>'
    '<tr><th>Left</th><th>Right</th></tr>'
    '<tr><td>A<br/>A′</td><td>45°</td><td>90°</td></tr></table>'
)


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected provider call")


def source_block(order, label, kind, text, **fields):
    return {"order": order, "label": label, "type": kind, "text": text, **fields}


def hosted_response(markdown, blocks):
    return {
        "id": "ocr-fixture",
        "model": "indic-ocr",
        "choices": [{"finish_reason": "stop", "index": 0,
                     "message": {"role": "assistant", "content": markdown}}],
        "blocks": blocks,
    }


@pytest.mark.parametrize("backend", ["hosted", "local-first"])
def test_structured_response_preserves_evidence_and_parses_both_backends(backend):
    blocks = [
        source_block(3, "Equation", "Equation", FORMULA),
        source_block(1, "Section-title", "SectionHeader", "Recognizing angles",
                     bbox_xyxy=[10, 12, 130, 35], conf=0.88),
        source_block(2, "List", "Text", "1. Find angle A\n   - Show your work"),
        source_block(4, "Table", "Table", TABLE),
        source_block(5, "Diagram", "Figure", ""),
        source_block(6, "Image-caption", "Caption", "Figure 1. An acute angle."),
    ]
    markdown = "Recognizing angles\n\n$$" + FORMULA + "$$"
    hosted = hosted_response(markdown, blocks)
    local = {"image": "page.png", "width": 160, "height": 200, "markdown": markdown,
             "blocks": blocks}
    attempts = AttemptLog()
    with httpx.Client(transport=httpx.MockTransport(
        (lambda _: httpx.Response(200, json=hosted)) if backend == "hosted" else forbidden
    )) as client:
        provider = IndicOCRProvider(
            backend, Inference(attempts, forbidden), client, {"BODHAN_OCR_KEY": "fake-key"},
            (lambda *_: local) if backend == "local-first" else forbidden, forbidden,
        )
        page = ocr_page(Page(4, Path("page.png")), b"normalized", provider.ocr, attempts)

    assert page.status == "ok"
    assert page.page_id == "p004" and page.input_index == 4
    assert page.image_digest == sha256(b"normalized").hexdigest()
    assert page.raw_response == (hosted if backend == "hosted" else local)
    assert page.raw_block_ids == tuple(f"p004-b{index:03d}" for index in range(1, 7))
    assert page.ordered_block_ids == ("p004-b002", "p004-b003", "p004-b001", *page.raw_block_ids[3:])
    assert page.attempt_ids == (page.attempts[0].id,)
    assert page.attempts[0].backend == ("hosted" if backend == "hosted" else "local")

    equation, heading, items, table, figure, caption = page.blocks
    assert equation.latex == FORMULA
    assert heading.heading_level == 2
    assert heading.bbox == (10, 12, 130, 35) and heading.confidence == 0.88
    assert [(item.marker, item.text, item.indent) for item in items.items] == [
        ("1.", "Find angle A", 0), ("-", "Show your work", 3),
    ]
    assert [[cell.text for cell in row] for row in table.rows] == [
        ["Region", "Angles"], ["Left", "Right"], ["A\nA′", "45°", "90°"],
    ]
    assert table.rows[0][0].rowspan == 2 and table.rows[0][1].colspan == 2
    assert table.rows[0][0].is_header is True
    assert table.rows[2][0].is_header is False
    assert figure.kind == "figure" and figure.flags == ()
    assert caption.caption_for == figure.id


def test_markdown_fallback_preserves_structure_and_has_no_geometry():
    equation = "\r\n " + FORMULA + " \r\n"
    markdown = (
        "## Angles ##\n\nA paragraph.\n\n- First\n- Second\n\n"
        "| Angle | Measure |\n| --- | ---: |\n| ABC | 90° |\n\n"
        + "$$" + equation + "$$\n\n"
        + "![Diagram](figure.png)\nFigure 1. An acute angle.\n\n"
        + "Exercises\n---------\n"
    )
    blocks = parse_ocr_response("p001", OCRResponse(markdown, {"blocks": []}, "", ()))
    assert [block.kind for block in blocks] == [
        "heading", "paragraph", "list", "table", "equation", "figure", "caption", "heading",
    ]
    assert all(block.bbox is None and block.confidence is None for block in blocks)
    assert blocks[0].text == "Angles" and blocks[0].heading_level == 2
    assert blocks[3].rows[1][1].text == "90°"
    assert blocks[4].latex == equation
    assert blocks[6].caption_for == blocks[5].id
    assert blocks[7].heading_level == 2


def test_caption_matches_aligned_figure_across_intervening_prose():
    raw = {"blocks": [
        source_block(0, "Diagram", "Figure", "", bbox_xyxy=[100, 20, 180, 80]),
        source_block(1, "Paragraph", "Text", "Nearby explanation.", bbox_xyxy=[10, 20, 90, 92]),
        source_block(2, "Image-caption", "Caption", "Fig. 6.3. Linear pair.",
                     bbox_xyxy=[104, 85, 176, 97]),
        source_block(3, "Image-caption", "Caption", "(i) Detail.", bbox_xyxy=[108, 56, 169, 65]),
        source_block(4, "Diagram", "Figure", "", bbox_xyxy=[10, 110, 88, 180]),
    ]}
    blocks = parse_ocr_response("p001", OCRResponse("", raw, "", ()))

    assert blocks[2].caption_for == blocks[3].caption_for == blocks[0].id


def test_caption_with_equally_aligned_figures_stays_unlinked():
    raw = {"blocks": [
        source_block(0, "Diagram", "Figure", "", bbox_xyxy=[100, 10, 190, 70]),
        source_block(1, "Image-caption", "Caption", "Fig. 6.3.", bbox_xyxy=[110, 75, 180, 85]),
        source_block(2, "Diagram", "Figure", "", bbox_xyxy=[100, 90, 190, 150]),
    ]}
    _, caption, _ = parse_ocr_response("p001", OCRResponse("", raw, "", ()))

    assert caption.caption_for is None


@pytest.mark.parametrize("text", [
    r"The answer is $90^\circ$.",
    r"Therefore, $\angle 1 = \angle 2$ and $\angle 1 = \angle 3$.",
    r"$$\angle A = 30^\circ$$" + "\n" + r"$$\angle B = 60^\circ$$",
])
def test_equation_label_preserves_balanced_mixed_math(text):
    raw = {"blocks": [source_block(0, "Equation", "Equation", text, conf=0.83)]}
    [block] = parse_ocr_response("p001", OCRResponse("", raw, "", ()))

    assert (block.id, block.kind, block.provider_label) == ("p001-b001", "paragraph", "Equation")
    assert block.text == text and block.latex is None
    assert block.flags == ()


def test_mixed_math_with_an_unmatched_delimiter_stays_flagged():
    text = r"The first angle is $90^\circ$, and the second is $45^\circ."
    raw = {"blocks": [source_block(0, "Equation", "Equation", text)]}
    [block] = parse_ocr_response("p001", OCRResponse("", raw, "", ()))

    assert block.text == text and "malformed_math" in block.flags


def test_empty_ocr_response_is_reported_as_a_failed_page():
    response = OCRResponse("", {"blocks": [], "markdown": ""}, "", ())
    page = ocr_page(Page(1, Path("p.png")), b"normalized", lambda *_: response, AttemptLog())

    assert page.status == "failed"
    assert page.reason == "OCR returned no blocks for this page."
    assert page.ordered_block_ids == () and page.raw_response == response.raw


@pytest.mark.parametrize("malformed", [r"$$\frac{1}{2$$", r"\[\sqrt{3}"])
def test_malformed_math_retains_text_and_allows_following_blocks(malformed):
    markdown = f"{malformed}\n\n## Next heading\n"
    response = OCRResponse(markdown, {"markdown": markdown}, "", ())
    page = ocr_page(Page(1, Path("p.png")), b"normalized", lambda *_: response, AttemptLog())
    formula, heading = page.blocks
    assert page.status == "partial"
    assert formula.text == malformed and "malformed_math" in formula.flags
    assert formula.latex
    assert heading.text == "Next heading"


@pytest.mark.parametrize("table", [
    "| Angle | Measure |\n| --- | --- |\n| ABC |",
    "| Angle | Measure |\n| ABC | 90° |",
    "<table><tr><td>Angle</td></tr>",
])
def test_malformed_table_retains_source_and_available_cells(table):
    response = OCRResponse(table, {"markdown": table}, "", ())
    [block] = parse_ocr_response("p001", response)
    assert block.kind == "table" and block.text == table
    assert block.rows and "malformed_table" in block.flags


@pytest.mark.parametrize("failure", ["http", "json", "schema"])
def test_failed_page_keeps_evidence_and_next_page_succeeds(tmp_path, failure):
    inputs = tmp_path / "input"
    inputs.mkdir()
    for index, color in enumerate(("white", "gray"), start=1):
        with Image.new("RGB", (15, 20), color) as page:
            page.save(inputs / f"page-{index}.png")
    pages = discover_pages(inputs)
    out = tmp_path / "out"
    preprocessed = preprocess_pages(pages, out, PreprocessSettings())
    success = httpx.Response(200, json=hosted_response("Success", []))
    if failure == "http":
        replies = [httpx.Response(503) for _ in range(3)]
    elif failure == "json":
        replies = [httpx.Response(200, content=b"invalid JSON")]
    else:
        replies = [httpx.Response(200, json=hosted_response("Diagnostic", {"wrong": []}))]
    replies = iter([*replies, success])
    attempts = AttemptLog()
    waits = []
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as client:
        provider = IndicOCRProvider(
            "hosted", Inference(attempts, waits.append), client,
            {"BODHAN_OCR_KEY": "fixture-secret"}, forbidden, forbidden,
        )
        failed, next_page = extract_pages(pages, preprocessed, out, provider.ocr, attempts)

    assert failed.status == "failed" and failed.ordered_block_ids == ()
    assert failed.reason and failed.image_digest == preprocessed[0].output_sha256
    assert len(failed.attempt_ids) == (3 if failure == "http" else 1)
    assert waits == ([1, 2] if failure == "http" else [])
    if failure == "schema":
        assert failed.raw_response["blocks"] == {}
    assert next_page.status == "ok" and next_page.ordered_block_ids == ("p002-b001",)
    saved = json.loads((out / "source/pages/p001.json").read_text(encoding="utf-8"))
    assert saved["status"] == "failed" and saved["ordered_block_ids"] == []
    assert [attempt["id"] for attempt in saved["attempts"]] == list(failed.attempt_ids)


def test_adapter_preserves_sanitized_raw_response_on_invalid_envelope():
    raw = hosted_response(None, [
        source_block(1, "Paragraph", "Text", "Read the scan. fixture-secret"),
    ])
    raw["headers"] = {"Authorization": "Bearer fixture-secret"}
    raw["request"] = {"image": "base64-image-echo"}
    attempts = AttemptLog()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw))) as client:
        provider = IndicOCRProvider(
            "hosted", Inference(attempts, forbidden), client,
            {"BODHAN_OCR_KEY": "fixture-secret"}, forbidden, forbidden,
        )
        page = ocr_page(Page(1, Path("page.png")), b"normalized", provider.ocr, attempts)

    assert page.status == "failed"
    assert page.attempts[0].error_category == "response"
    assert page.raw_response["blocks"][0]["text"] == "Read the scan. [redacted]"
    assert "headers" not in page.raw_response and "request" not in page.raw_response


def test_ocr_checkpoint_reuses_exact_samples_and_continues_if_language_unresolved(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "hosted")
    monkeypatch.setenv("BODHAN_OCR_KEY", "fixture-secret")
    for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for index, color in enumerate(("white", "gray"), start=1):
        with Image.new("RGB", (15, 20), color) as image:
            image.save(tmp_path / f"page-{index}.png")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=hosted_response("Readable text.", []))

    create_client = httpx.Client
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: create_client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    out = tmp_path / "out"
    assert main(["--input", str(tmp_path), "--langs", "hi", "--out", str(out), "--until", "ocr"]) == 0
    assert len(requests) == 2
    language = json.loads((out / "source/language.json").read_text(encoding="utf-8"))
    assert language["code"] is None
    for index, attempt in enumerate(language["attempts"], start=1):
        page = json.loads((out / f"source/pages/p{index:03d}.json").read_text(encoding="utf-8"))
        assert page["status"] == "ok"
        assert page["attempt_ids"] == [attempt["id"]]
