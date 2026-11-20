"""Tests for OCR page loop, multi-page PDFs, and page order preservation (Slice 7)."""

from pathlib import Path

import httpx
import pymupdf  # PyMuPDF
import pytest

from bodhan_mcp import errors
from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient
from bodhan_mcp.schemas import OcrDocOut, OcrPageOut
from bodhan_mcp.tools.ocr import ocr_document, ocr_page


@pytest.fixture
def three_page_pdf(tmp_path: Path) -> Path:
    """Generate a valid 3-page PDF document."""
    pdf_path = tmp_path / "three_pages.pdf"
    doc = pymupdf.open()
    for i in range(1, 4):
        page = doc.new_page(width=300, height=200)
        page.insert_text((50, 50), f"Content of Page {i}")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.mark.asyncio
async def test_ocr_page_missing_and_success(sample_image: Path, tmp_path: Path):
    """Verify ocr_page handles missing images and executes correctly on valid images."""
    err = await ocr_page(str(tmp_path / "missing.png"))
    assert isinstance(err, errors.ToolErrorPayload)
    assert err.code == errors.FILE_NOT_FOUND

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"markdown": "| Col1 | Col2 |\n|---|---|\n| A | B |"})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    out = await ocr_page(str(sample_image), client=bodhan)
    assert isinstance(out, OcrPageOut)
    assert "| Col1 | Col2 |" in out.markdown
    assert out.cost_inr == 0.20
    await base_client.close()


@pytest.mark.asyncio
async def test_ocr_document_page_loop(three_page_pdf: Path):
    """Verify ocr_document loops over N pages, preserving page order and aggregating cost."""
    ocr_calls = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        ocr_calls.append(len(ocr_calls) + 1)
        return httpx.Response(200, json={"markdown": f"# Page {len(ocr_calls)} Heading\n\nPage text."})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    res = await ocr_document(str(three_page_pdf), client=bodhan)
    assert isinstance(res, OcrDocOut)
    assert res.pages == 3
    # N pages => exactly N model calls
    assert len(ocr_calls) == 3
    # Invariant: order preserved in concatenated markdown
    assert "# Page 1 Heading" in res.markdown
    assert "# Page 2 Heading" in res.markdown
    assert "# Page 3 Heading" in res.markdown
    assert res.markdown.index("Page 1") < res.markdown.index("Page 2") < res.markdown.index("Page 3")
    # Invariant: cost = 3 * 0.20 = 0.60 INR
    assert res.cost_inr == 0.60
    await base_client.close()
