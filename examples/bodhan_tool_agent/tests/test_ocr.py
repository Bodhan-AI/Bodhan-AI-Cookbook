"""Tests for Indic-OCR MCP tool using httpx.MockTransport (Zero Live API Calls)."""

import json
from pathlib import Path
import httpx
import pytest

from bodhan_mcp.tools.ocr import IndicOcrTool


@pytest.fixture
def tmp_ocr_cache(tmp_path: Path):
    """Provides an isolated cache directory for OCR tests."""
    cache_dir = tmp_path / "test_ocr_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.mark.asyncio
async def test_ocr_single_page_image(tmp_ocr_cache: Path):
    """Parses a single-page PNG image, returns text and cost."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "# Heading\n\nPage text parsed cleanly.",
                    }
                }
            ]
        }
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    img_path = Path("tests/fixtures/sample_page.png")
    result = await tool.parse_document(img_path)

    assert result.success is True
    assert result.total_pages_in_doc == 1
    assert result.pages_processed == 1
    assert len(result.pages) == 1
    assert result.pages[0].page_number == 1
    assert "Page text parsed cleanly" in result.pages[0].text
    assert result.pages[0].cache_hit is False
    assert result.billed_pages_count == 1
    assert result.estimated_cost_inr == 0.20
    assert call_count == 1


@pytest.mark.asyncio
async def test_ocr_3page_pdf(tmp_ocr_cache: Path):
    """Renders 3-page PDF and makes one request per page in order."""
    calls_payload = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls_payload.append(body)
        idx = len(calls_payload)
        data = {"choices": [{"message": {"role": "assistant", "content": f"Text from page {idx}"}}]}
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    pdf_path = Path("tests/fixtures/sample_3pages.pdf")
    result = await tool.parse_document(pdf_path)

    assert result.success is True
    assert result.total_pages_in_doc == 3
    assert result.pages_processed == 3
    assert result.capped is False
    assert len(result.pages) == 3
    for i, p in enumerate(result.pages, 1):
        assert p.page_number == i
        assert f"Text from page {i}" in p.text
        assert p.cache_hit is False

    assert result.billed_pages_count == 3
    assert result.cached_pages_count == 0
    assert result.estimated_cost_inr == pytest.approx(0.60)
    assert len(calls_payload) == 3


@pytest.mark.asyncio
async def test_ocr_cache_hit_per_page(tmp_ocr_cache: Path):
    """Subsequent call on identical image/PDF must hit cache with 0 HTTP calls and Rs 0.00 cost."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        data = {"choices": [{"message": {"role": "assistant", "content": "OCR result"}}]}
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    img_path = Path("tests/fixtures/sample_page.png")

    # Call 1: Miss
    res1 = await tool.parse_document(img_path)
    assert res1.success is True
    assert res1.pages[0].cache_hit is False
    assert res1.billed_pages_count == 1
    assert call_count == 1

    # Call 2: Hit
    res2 = await tool.parse_document(img_path)
    assert res2.success is True
    assert res2.pages[0].cache_hit is True
    assert res2.cached_pages_count == 1
    assert res2.billed_pages_count == 0
    assert res2.estimated_cost_inr == 0.0
    assert call_count == 1  # No new calls made!


@pytest.mark.asyncio
async def test_ocr_502_error_no_blind_retry(tmp_ocr_cache: Path):
    """HTTP 502 decoding error must return StructuredError and not retry blindly."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(502, text="Bad Gateway: indic-ocr page could not be transcribed")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    img_path = Path("tests/fixtures/sample_page.png")
    result = await tool.parse_document(img_path)

    assert result.success is False
    assert result.error is not None
    assert result.error.code in ("PAGE_DECODE_ERROR", "BAD_GATEWAY")
    assert result.error.retryable is False
    assert call_count == 1  # Did NOT retry blindly!


@pytest.mark.asyncio
async def test_ocr_bad_path(tmp_ocr_cache: Path):
    """Non-existent file path returns StructuredError before any HTTP calls."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    result = await tool.parse_document("non_existent_file.png")
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "FILE_NOT_FOUND"
    assert call_count == 0


@pytest.mark.asyncio
async def test_ocr_unsupported_type(tmp_ocr_cache: Path, tmp_path: Path):
    """Unsupported file extension returns StructuredError before any HTTP calls."""
    txt_file = tmp_path / "document.txt"
    txt_file.write_text("Hello world")

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    result = await tool.parse_document(txt_file)
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "UNSUPPORTED_MEDIA_TYPE"
    assert call_count == 0


@pytest.mark.asyncio
async def test_ocr_pdf_page_capping(tmp_ocr_cache: Path):
    """PDF with max_pages < total_pages caps processing and reports capped=True."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "Text"}}]}
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    pdf_path = Path("tests/fixtures/sample_3pages.pdf")
    # Cap at 2 pages
    result = await tool.parse_document(pdf_path, max_pages=2)

    assert result.success is True
    assert result.total_pages_in_doc == 3
    assert result.pages_processed == 2
    assert result.capped is True
    assert len(result.pages) == 2
    assert call_count == 2
    assert result.billed_pages_count == 2
    assert result.estimated_cost_inr == pytest.approx(0.40)


def test_ocr_preflight_cost_estimate():
    """estimate_cost reports page count and cost without any HTTP calls."""
    tool = IndicOcrTool(api_key="test_key")

    # 1. Image
    est_img = tool.estimate_cost(Path("tests/fixtures/sample_page.png"))
    assert est_img["total_pages"] == 1
    assert est_img["pages_to_process"] == 1
    assert est_img["capped"] is False
    assert est_img["estimated_cost_inr"] == 0.20

    # 2. PDF (3 pages)
    est_pdf = tool.estimate_cost(Path("tests/fixtures/sample_3pages.pdf"))
    assert est_pdf["total_pages"] == 3
    assert est_pdf["pages_to_process"] == 3
    assert est_pdf["capped"] is False
    assert est_pdf["estimated_cost_inr"] == pytest.approx(0.60)

    # 3. PDF with capping (max_pages=2)
    est_capped = tool.estimate_cost(Path("tests/fixtures/sample_3pages.pdf"), max_pages=2)
    assert est_capped["total_pages"] == 3
    assert est_capped["pages_to_process"] == 2
    assert est_capped["capped"] is True
    assert est_capped["estimated_cost_inr"] == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_ocr_preserves_latex_backslash_sequences(tmp_ocr_cache: Path):
    """Preserves LaTeX mathematical notation with literal backslashes intact ($a \\neq b$)."""
    latex_content = "$a \\neq b$ and \\nabla f"

    def handler(request: httpx.Request) -> httpx.Response:
        data = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": latex_content},
                }
            ]
        }
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    img_path = Path("tests/fixtures/sample_page.png")
    res = await tool.parse_document(img_path)
    assert res.success is True
    assert res.pages[0].text == latex_content
    assert "$a \\neq b$" in res.pages[0].text


@pytest.mark.asyncio
async def test_ocr_multipage_partial_page_failure(tmp_ocr_cache: Path):
    """If one page of a multi-page PDF fails, document does not halt; returns successful pages."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            # Page 2 fails with 502 Bad Gateway
            return httpx.Response(502, text="Bad Gateway: indic-ocr page could not be transcribed")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": f"Text from page {call_count}"},
                    }
                ]
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    pdf_path = Path("tests/fixtures/sample_3pages.pdf")
    result = await tool.parse_document(pdf_path)

    assert result.success is True
    assert result.total_pages_in_doc == 3
    assert result.pages_processed == 3
    assert len(result.pages) == 3

    # Page 1 succeeded
    assert result.pages[0].page_number == 1
    assert result.pages[0].text == "Text from page 1"
    assert result.pages[0].error is None

    # Page 2 failed with StructuredError
    assert result.pages[1].page_number == 2
    assert result.pages[1].text == ""
    assert result.pages[1].error is not None
    assert result.pages[1].error.code in ("PAGE_DECODE_ERROR", "BAD_GATEWAY")

    # Page 3 succeeded
    assert result.pages[2].page_number == 3
    assert result.pages[2].text == "Text from page 3"
    assert result.pages[2].error is None

    # Billed only for the 2 successful pages
    assert result.billed_pages_count == 2
    assert result.estimated_cost_inr == pytest.approx(0.40)
    assert "Text from page 1" in result.full_text
    assert "Text from page 3" in result.full_text


@pytest.mark.asyncio
async def test_ocr_truncation_finish_reason_length(tmp_ocr_cache: Path):
    """When finish_reason == 'length', return StructuredError and do not cache."""

    def handler(request: httpx.Request) -> httpx.Response:
        data = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"role": "assistant", "content": "Cut off text..."},
                }
            ]
        }
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicOcrTool(api_key="test_key", client=mock_client, cache_dir=tmp_ocr_cache)

    img_path = Path("tests/fixtures/sample_page.png")
    res = await tool.parse_document(img_path)

    assert res.success is False
    assert res.error is not None
    assert res.error.code == "PAGE_TRUNCATED"
    assert res.error.retryable is False
    # Cache must remain empty
    cache_files = list(tmp_ocr_cache.glob("*.json"))
    assert len(cache_files) == 0
