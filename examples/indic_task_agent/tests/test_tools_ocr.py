"""ocr_document over the fake backend."""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from bodhan_mcp.core.errors import BodhanError
from bodhan_mcp.tools.ocr import ocr_document

BAD_BLOCK = {"error": {"code": "bad_gateway", "message": "block exceeded token budget"}}


def test_every_page_is_one_request_in_order(bodhan, make_pdf, tmp_path):
    res = ocr_document(str(make_pdf("d.pdf", 8)), out_dir=str(tmp_path / "o"))
    reqs = bodhan.of("ocr")
    assert len(reqs) == len(bodhan.requests) == 8
    assert {r.model for r in reqs} == {"indic-ocr"}
    assert res.page_count == 8 and [p.index for p in res.pages] == list(range(8))
    sections = Path(res.markdown_path).read_text(encoding="utf-8").split("\n\n---\n\n")
    assert len(sections) == 8
    for i, section in enumerate(sections, 1):
        # block order 0 (the section title) is emitted before order 1 even though it arrives second
        assert section == f"## Heading {i}\n\nBody of ocr call {i}."
    assert res.cost_inr == pytest.approx(1.6)


def test_page_selection(bodhan, make_pdf, tmp_path):
    res = ocr_document(str(make_pdf("d.pdf", 8)), pages="2,5", out_dir=str(tmp_path / "o"))
    assert len(bodhan.of("ocr")) == 2
    assert [p.index for p in res.pages] == [1, 4]


def test_request_is_a_jpeg_data_url_but_the_result_has_no_base64(bodhan, make_pdf, tmp_path):
    res = ocr_document(str(make_pdf("d.pdf", 1)), out_dir=str(tmp_path / "o"))
    (req,) = bodhan.of("ocr")
    part = req.json["messages"][0]["content"][0]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    dumped = json.dumps(res.model_dump())
    assert "base64" not in dumped and "data:image" not in dumped and len(dumped) < 2000


def test_502_is_retried_once_with_a_bigger_allowance(bodhan, make_pdf, tmp_path):
    bodhan.script("ocr", bodhan.error(502, BAD_BLOCK))
    res = ocr_document(str(make_pdf("d.pdf", 1)), out_dir=str(tmp_path / "o"))
    first, second = bodhan.of("ocr")
    assert first.json["max_tokens"] == 2048 and second.json["max_tokens"] >= 4096
    assert any("retried_at_4096" in w for w in res.warnings)
    assert res.pages[0].retried_max_tokens


def test_exhausted_retries_raise_a_named_error(bodhan, make_pdf, tmp_path):
    # the ladder is 2048 -> 4096 -> 8192; every rung failing is OCR_FAILED
    bodhan.script("ocr", *[bodhan.error(502, BAD_BLOCK) for _ in range(3)])
    with pytest.raises(BodhanError) as exc:
        ocr_document(str(make_pdf("d.pdf", 1)), out_dir=str(tmp_path / "o"))
    assert exc.value.code in ("OCR_FAILED", "UPSTREAM_ERROR")
    assert "8192" in exc.value.message
    assert [r.json["max_tokens"] for r in bodhan.of("ocr")] == [2048, 4096, 8192]


def test_two_502s_still_recover_on_the_last_rung(bodhan, make_pdf, tmp_path):
    bodhan.script("ocr", bodhan.error(502, BAD_BLOCK), bodhan.error(502, BAD_BLOCK))
    res = ocr_document(str(make_pdf("d.pdf", 1)), out_dir=str(tmp_path / "o"))
    assert [r.json["max_tokens"] for r in bodhan.of("ocr")] == [2048, 4096, 8192]
    assert res.pages[0].retried_max_tokens


def test_rerun_after_a_page_failure_pays_only_that_page(bodhan, make_pdf, tmp_path):
    pdf = make_pdf("d.pdf", 3)
    bodhan.script("ocr", None, None, *[bodhan.error(502, BAD_BLOCK) for _ in range(3)])
    with pytest.raises(BodhanError):
        ocr_document(str(pdf), out_dir=str(tmp_path / "o"))
    before = len(bodhan.of("ocr"))
    res = ocr_document(str(pdf), out_dir=str(tmp_path / "o"))
    assert len(bodhan.of("ocr")) - before == 1
    assert [p.cache_hit for p in res.pages] == [True, True, False]
    assert res.cost_inr == pytest.approx(0.2)


def test_image_input_is_one_request(bodhan, tmp_path):
    png = tmp_path / "scan.png"
    Image.new("RGB", (120, 80), "white").save(png)
    res = ocr_document(str(png), out_dir=str(tmp_path / "o"))
    assert len(bodhan.of("ocr")) == 1 and res.page_count == 1


def test_encrypted_pdf(bodhan, tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "secret")
    path = tmp_path / "locked.pdf"
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u-pass", owner_pw="o-pass")
    with pytest.raises(BodhanError) as exc:
        ocr_document(str(path), out_dir=str(tmp_path / "o"))
    assert exc.value.code == "PDF_ENCRYPTED"
    assert bodhan.requests == []


def test_unsupported_suffix(bodhan, tmp_path):
    f = tmp_path / "notes.docx"
    f.write_bytes(b"x")
    with pytest.raises(BodhanError) as exc:
        ocr_document(str(f), out_dir=str(tmp_path / "o"))
    assert exc.value.code == "UNSUPPORTED_FORMAT"
    assert bodhan.requests == []
