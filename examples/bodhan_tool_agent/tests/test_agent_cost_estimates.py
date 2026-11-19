"""Focused tests for agent-side Bodhan cost unit estimates."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.bodhan_agent.agent import _estimate_units
from tests.test_document import (
    create_synthetic_docx,
    create_synthetic_pdf,
    create_synthetic_tiff,
)


@pytest.mark.parametrize(
    ("page_limit", "expected_pages"),
    [(None, 4.0), (2, 2.0), (0, 4.0)],
)
def test_ocr_estimate_uses_document_page_count(
    tmp_path: Path,
    page_limit: int | None,
    expected_pages: float,
) -> None:
    pdf_path = create_synthetic_pdf(tmp_path / "four_pages.pdf", num_pages=4)

    assert _estimate_units(
        "extract_document_text",
        {"file_path": str(pdf_path), "page_limit": page_limit},
    ) == ("indic-ocr", expected_pages)


def test_docx_ocr_estimate_is_zero(tmp_path: Path) -> None:
    docx_path = create_synthetic_docx(tmp_path / "local.docx")

    assert _estimate_units(
        "extract_document_text",
        {"file_path": str(docx_path)},
    ) == ("indic-ocr", 0.0)


def test_multipage_tiff_ocr_estimate_uses_all_frames(tmp_path: Path) -> None:
    tiff_path = create_synthetic_tiff(tmp_path / "two_pages.tiff", num_pages=2)

    assert _estimate_units(
        "extract_document_text",
        {"file_path": str(tiff_path)},
    ) == ("indic-ocr", 2.0)


def test_ocr_estimate_fails_closed_when_page_count_is_unavailable(tmp_path: Path) -> None:
    unreadable_pdf = tmp_path / "unreadable.pdf"
    unreadable_pdf.write_bytes(b"not a PDF")

    with pytest.raises(ToolError, match="refusing to send a paid OCR request"):
        _estimate_units("extract_document_text", {"file_path": str(unreadable_pdf)})


def test_empty_translation_has_no_bodhan_cost() -> None:
    assert _estimate_units("translate_text", {"text": ""}) == ("indic-translate", 0.0)
