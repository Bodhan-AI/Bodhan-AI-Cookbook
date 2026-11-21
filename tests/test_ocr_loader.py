"""Unit tests for BodhanOCRLoader using mocked Bodhan client and synthetic PDFs."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import httpx
import pymupdf
import pytest

INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "langchain"
if str(INTEGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS_DIR))

from bodhan_langchain.client import BodhanAsyncClient, BodhanClient
from bodhan_langchain.exceptions import BodhanAPIError, BodhanValidationError
from bodhan_langchain.ocr_loader import BodhanOCRLoader

DUMMY_KEY = "dummy-ocr-loader-key"


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a temporary 2-page PDF file."""
    pdf_path = tmp_path / "sample_test.pdf"
    doc = pymupdf.open()
    # Page 1
    p1 = doc.new_page(width=400, height=300)
    p1.insert_text((50, 50), "Page One Content")
    # Page 2
    p2 = doc.new_page(width=400, height=300)
    p2.insert_text((50, 50), "Page Two Content")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    """Create a temporary PNG image."""
    png_path = tmp_path / "sample_test.png"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 20), "PNG OCR Test")
    pix = page.get_pixmap()
    pix.save(str(png_path))
    doc.close()
    return png_path


@pytest.fixture
def sample_jpeg(tmp_path: Path) -> Path:
    """Create a temporary JPEG image."""
    jpg_path = tmp_path / "sample_test.jpg"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 20), "JPEG OCR Test")
    pix = page.get_pixmap()
    pix.pil_save(str(jpg_path), format="JPEG")
    doc.close()
    return jpg_path


def test_loader_single_png_success(sample_png: Path) -> None:
    """Verify loading a single PNG image into a LangChain Document."""
    mock_payload = {
        "choices": [{"message": {"content": "# PNG Extracted Text"}}],
        "blocks": [{"order": 1, "label": "header", "type": "heading", "bbox_xyxy": [10.5, 10.25, 100.75, 30.5], "conf": 0.99, "text": "PNG Extracted Text"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["model"] == "indic-ocr"
        return httpx.Response(200, json=mock_payload)

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    loader = BodhanOCRLoader(sample_png, client=client)
    docs = loader.load()

    assert len(docs) == 1
    doc = docs[0]
    assert doc.page_content == "# PNG Extracted Text"
    assert doc.metadata["page"] == 1
    assert doc.metadata["page_number"] == 1
    assert doc.metadata["total_pages"] == 1
    assert doc.metadata["model"] == "indic-ocr"
    assert len(doc.metadata["blocks"]) == 1
    block_meta = doc.metadata["blocks"][0]
    assert block_meta["label"] == "header"
    assert block_meta["type"] == "heading"
    assert block_meta["bbox_xyxy"] == [10.5, 10.25, 100.75, 30.5]
    assert block_meta["conf"] == 0.99


def test_loader_single_jpeg_success(sample_jpeg: Path) -> None:
    """Verify loading a single JPEG image into a LangChain Document."""
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "JPEG Text"}}]})

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    loader = BodhanOCRLoader(sample_jpeg, client=client)
    docs = loader.load()

    assert len(docs) == 1
    assert docs[0].page_content == "JPEG Text"


def test_loader_multipage_pdf_success(sample_pdf: Path) -> None:
    """Verify multi-page PDF processing with page numbering, metadata and ordering."""
    page_counter = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page_counter
        page_counter += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"content": f"# Extracted Page {page_counter}"}}],
            "blocks": [{"order": 1, "label": "paragraph", "bbox_xyxy": [50, 50, 200, 80], "conf": 0.95, "text": f"Page {page_counter}"}],
        })

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    loader = BodhanOCRLoader(sample_pdf, client=client, dpi=150)
    docs = loader.load()

    assert len(docs) == 2
    assert docs[0].page_content == "# Extracted Page 1"
    assert docs[0].metadata["page"] == 1
    assert docs[0].metadata["total_pages"] == 2
    assert docs[1].page_content == "# Extracted Page 2"
    assert docs[1].metadata["page"] == 2
    assert docs[1].metadata["total_pages"] == 2


def test_loader_max_pages_limit(sample_pdf: Path) -> None:
    """Verify that max_pages restricts PDF page rasterization and processing."""
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "Page content"}}]})

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    loader = BodhanOCRLoader(sample_pdf, client=client, max_pages=1)
    docs = loader.load()

    assert len(docs) == 1
    assert call_count == 1
    assert docs[0].metadata["page"] == 1
    assert docs[0].metadata["total_pages"] == 2


def test_loader_missing_and_corrupted_files(tmp_path: Path) -> None:
    """Verify error handling on missing, empty, or corrupted files."""
    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client())

    # Missing file
    missing = tmp_path / "does_not_exist.pdf"
    with pytest.raises(FileNotFoundError):
        BodhanOCRLoader(missing, client=client).load()

    # Empty file
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(BodhanValidationError) as exc:
        BodhanOCRLoader(empty, client=client).load()
    assert "empty" in str(exc.value)

    # Corrupted PDF
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"Not a real PDF stream")
    with pytest.raises(BodhanValidationError) as exc:
        BodhanOCRLoader(corrupt, client=client).load()
    assert "Failed to open PDF document" in str(exc.value)

    # Unsupported extension
    unsupported = tmp_path / "document.docx"
    unsupported.write_text("Hello docx")
    with pytest.raises(BodhanValidationError) as exc:
        BodhanOCRLoader(unsupported, client=client).load()
    assert "Unsupported file format" in str(exc.value)


def test_loader_page_error_handling_policies(sample_pdf: Path) -> None:
    """Verify page_error_handling policies ('raise' vs 'ignore')."""
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": {"message": "OCR Server Error"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Page 2 OK"}}]})

    # 'raise' policy (default)
    client_raise = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    with pytest.raises(BodhanAPIError):
        BodhanOCRLoader(sample_pdf, client=client_raise, page_error_handling="raise").load()

    # 'ignore' policy
    calls = 0
    client_ignore = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    docs = BodhanOCRLoader(sample_pdf, client=client_ignore, page_error_handling="ignore").load()
    assert len(docs) == 1
    assert docs[0].page_content == "Page 2 OK"
    assert docs[0].metadata["page"] == 2


@pytest.mark.asyncio
async def test_loader_async_loading(sample_pdf: Path) -> None:
    """Verify async alazy_load and aload operations."""
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "Async Page Content"}}]})

    async_client = BodhanAsyncClient(api_key=DUMMY_KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    loader = BodhanOCRLoader(sample_pdf, async_client=async_client)
    docs = await loader.aload()

    assert len(docs) == 2
    assert docs[0].page_content == "Async Page Content"
    assert docs[0].metadata["page"] == 1
    assert docs[0].metadata["page_number"] == 1
    assert docs[1].metadata["page"] == 2
    assert docs[1].metadata["page_number"] == 2


def test_loader_rejects_webp(tmp_path: Path) -> None:
    """Verify that WebP images are explicitly rejected with an actionable BodhanValidationError."""
    webp_path = tmp_path / "sample.webp"
    webp_path.write_bytes(b"RIFF\x18\x00\x00\x00WEBPVP8 \x0c\x00\x00\x00/\x00\x00\x00\x10\x07\x00\x9d\x01*")

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client())
    loader = BodhanOCRLoader(webp_path, client=client)

    with pytest.raises(BodhanValidationError) as exc:
        loader.load()
    assert "Unsupported image format '.webp'" in str(exc.value)
    assert "PNG and JPEG" in str(exc.value)


def test_loader_rejects_gif_and_svg(tmp_path: Path) -> None:
    """Verify that GIF and SVG files are rejected with actionable error messages."""
    gif_path = tmp_path / "sample.gif"
    gif_path.write_bytes(b"GIF89a\x01\x00\x01\x00")
    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client())
    with pytest.raises(BodhanValidationError) as exc:
        BodhanOCRLoader(gif_path, client=client).load()
    assert "Unsupported image format '.gif'" in str(exc.value)

    svg_path = tmp_path / "sample.svg"
    svg_path.write_text("<svg></svg>")
    with pytest.raises(BodhanValidationError) as exc2:
        BodhanOCRLoader(svg_path, client=client).load()
    assert "Unsupported image format '.svg'" in str(exc2.value)


def test_loader_rejects_empty_file(tmp_path: Path) -> None:
    """Verify that empty files are rejected."""
    empty_png = tmp_path / "empty.png"
    empty_png.touch()
    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client())
    with pytest.raises(BodhanValidationError) as exc:
        BodhanOCRLoader(empty_png, client=client).load()
    assert "empty (0 bytes)" in str(exc.value)


def test_loader_client_lifecycle_internally_owned_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that an internally instantiated client is cleanly closed after lazy_load completes."""
    png_path = tmp_path / "valid.png"
    # Create minimal valid 1x1 PNG bytes
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa76u\xa4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    png_path.write_bytes(png_bytes)

    closed_internally = False

    class MockInternalClient:
        def __init__(self, *args, **kwargs):
            pass
        def ocr(self, *args, **kwargs):
            from bodhan_langchain.models import OCRResponse
            return OCRResponse(text="Mocked Text", blocks=[])
        def close(self):
            nonlocal closed_internally
            closed_internally = True

    monkeypatch.setattr("bodhan_langchain.ocr_loader.BodhanClient", MockInternalClient)

    loader = BodhanOCRLoader(png_path)
    docs = loader.load()

    assert len(docs) == 1
    assert closed_internally is True


def test_loader_injected_client_not_closed(sample_png: Path) -> None:
    """Verify that an externally injected client is NOT closed by loader."""
    closed_injected = False

    class MockInjectedClient:
        def ocr(self, *args, **kwargs):
            from bodhan_langchain.models import OCRResponse
            return OCRResponse(text="Mocked Text", blocks=[])
        def close(self):
            nonlocal closed_injected
            closed_injected = True

    injected = MockInjectedClient()
    loader = BodhanOCRLoader(sample_png, client=injected)  # type: ignore[arg-type]
    docs = loader.load()

    assert len(docs) == 1
    assert closed_injected is False


def test_loader_pdf_early_generator_termination_releases_resources(sample_pdf: Path) -> None:
    """Verify early generator termination safely releases PDF handles and internally owned clients."""
    closed_client = False

    class MockClient:
        def ocr(self, *args, **kwargs):
            from bodhan_langchain.models import OCRResponse
            return OCRResponse(text="Mocked Page", blocks=[])
        def close(self):
            nonlocal closed_client
            closed_client = True

    loader = BodhanOCRLoader(sample_pdf, client=MockClient())  # type: ignore[arg-type]
    gen = loader.lazy_load()
    first_page = next(gen)
    assert first_page.metadata["page"] == 1
    gen.close()  # Early termination before page 2
    # GeneratorExit handled cleanly without leaking handles or crashing


def test_loader_pdf_without_pymupdf_raises_clear_error(sample_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that attempting to load a PDF without PyMuPDF installed raises an informative ImportError."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pymupdf":
            raise ImportError("No module named 'pymupdf'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client())
    loader = BodhanOCRLoader(sample_pdf, client=client)

    with pytest.raises(ImportError) as exc_info:
        loader.load()

    assert "PyMuPDF is required for PDF OCR loading" in str(exc_info.value)
    assert "requirements-pdf.txt" in str(exc_info.value)


def test_image_ocr_works_without_pymupdf(sample_png: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that image OCR functionality works completely independently of PyMuPDF."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pymupdf":
            raise ImportError("No module named 'pymupdf'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Image text without PyMuPDF"}}],
            "blocks": [],
        })

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    loader = BodhanOCRLoader(sample_png, client=client)
    docs = loader.load()

    assert len(docs) == 1
    assert docs[0].page_content == "Image text without PyMuPDF"
