"""Unit tests for the Document Engine (src.common.document)."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
import fitz
from PIL import Image
import pytest

from src.common.document import (
    extract_docx_text,
    get_document_metadata,
    get_pdf_metadata,
    rasterize_document_pages,
    rasterize_pdf_pages,
)


def create_synthetic_pdf(path: Path, num_pages: int = 3) -> Path:
    """Create a synthetic PDF with text and a mock table using PyMuPDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()

    for i in range(1, num_pages + 1):
        page = doc.new_page(width=595, height=842)  # A4 size
        text = f"Sample Page {i}\nBodhan AI Indic-OCR Testing Document\n"
        page.insert_text((50, 72), text, fontsize=14)
        page.draw_rect(fitz.Rect(50, 120, 500, 300), color=(0, 0, 1), width=1.5)

    doc.save(str(path))
    doc.close()
    return path


def create_synthetic_tiff(path: Path, num_pages: int = 2) -> Path:
    """Create a synthetic multi-page TIFF image using PIL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(num_pages):
        im = Image.new("RGB", (200, 200), color=(i * 50, 100, 150))
        frames.append(im)
    frames[0].save(
        str(path),
        save_all=True,
        append_images=frames[1:],
        format="TIFF",
    )
    return path


def create_synthetic_docx(path: Path) -> Path:
    """Create a synthetic .docx file using standard library zipfile and XML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    xml_content = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        '  <w:body>\n'
        '    <w:p><w:r><w:t>Indic AI Cookbook Overview</w:t></w:r></w:p>\n'
        '    <w:p><w:r><w:t>नमस्ते भारत! यह बोधन एआई परीक्षण दस्तावेज़ है।</w:t></w:r></w:p>\n'
        '    <w:tbl>\n'
        '      <w:tr>\n'
        '        <w:tc><w:p><w:r><w:t>Model</w:t></w:r></w:p></w:tc>\n'
        '        <w:tc><w:p><w:r><w:t>Modality</w:t></w:r></w:p></w:tc>\n'
        '      </w:tr>\n'
        '      <w:tr>\n'
        '        <w:tc><w:p><w:r><w:t>indic-transcribe</w:t></w:r></w:p></w:tc>\n'
        '        <w:tc><w:p><w:r><w:t>ASR</w:t></w:r></w:p></w:tc>\n'
        '      </w:tr>\n'
        '      <w:tr>\n'
        '        <w:tc><w:p><w:r><w:t>indic-translate</w:t></w:r></w:p></w:tc>\n'
        '        <w:tc><w:p><w:r><w:t>MT</w:t></w:r></w:p></w:tc>\n'
        '      </w:tr>\n'
        '    </w:tbl>\n'
        '    <w:p><w:r><w:t>End of Document.</w:t></w:r></w:p>\n'
        '  </w:body>\n'
        '</w:document>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml_content.encode("utf-8"))
    return path


def test_pdf_metadata(tmp_path: Path):
    pdf_path = create_synthetic_pdf(tmp_path / "test_doc.pdf", num_pages=3)
    meta = get_pdf_metadata(pdf_path)
    assert meta["page_count"] == 3
    assert not meta["is_encrypted"]
    assert "PDF" in meta["format"]


def test_pdf_rasterization(tmp_path: Path):
    pdf_path = create_synthetic_pdf(tmp_path / "test_doc.pdf", num_pages=4)
    out_dir = tmp_path / "rasterized"

    # Rasterize only first 2 pages
    images = rasterize_pdf_pages(pdf_path, output_dir=out_dir, dpi=150, page_limit=2)
    assert len(images) == 2
    for img in images:
        assert img.exists()
        assert img.suffix == ".png"
        assert img.stat().st_size > 1000  # Non-empty image

    # Rasterize all pages
    all_images = rasterize_pdf_pages(pdf_path, output_dir=out_dir, dpi=150)
    assert len(all_images) == 4


def test_multi_format_rasterization_tiff(tmp_path: Path):
    tiff_path = create_synthetic_tiff(tmp_path / "multipage.tiff", num_pages=2)
    out_dir = tmp_path / "rasterized_tiff"

    images = rasterize_document_pages(tiff_path, output_dir=out_dir, dpi=150)
    assert len(images) == 2
    for img in images:
        assert img.exists()
        assert img.suffix == ".png"


def test_docx_extraction_markdown(tmp_path: Path):
    docx_path = create_synthetic_docx(tmp_path / "sample.docx")
    text = extract_docx_text(docx_path, table_format="markdown")

    assert "Indic AI Cookbook Overview" in text
    assert "नमस्ते भारत! यह बोधन एआई परीक्षण दस्तावेज़ है।" in text
    # Verify markdown table
    assert "| Model | Modality |" in text
    assert "| --- | --- |" in text
    assert "| indic-transcribe | ASR |" in text
    assert "| indic-translate | MT |" in text
    assert "End of Document." in text


def test_docx_extraction_html(tmp_path: Path):
    docx_path = create_synthetic_docx(tmp_path / "sample.docx")
    text = extract_docx_text(docx_path, table_format="html")

    assert "Indic AI Cookbook Overview" in text
    assert "<table>" in text
    assert "<tr><th>Model</th><th>Modality</th></tr>" in text
    assert "<tr><td>indic-transcribe</td><td>ASR</td></tr>" in text
    assert "</table>" in text


def test_docx_metadata(tmp_path: Path):
    docx_path = create_synthetic_docx(tmp_path / "sample.docx")
    meta = get_document_metadata(docx_path)
    assert meta["format"] == "DOCX"
    assert meta["type"] == "document"
    assert meta["paragraph_count"] >= 3
    assert meta["table_count"] == 1
    assert meta["page_count"] == 1


def test_invalid_and_corrupt_docx(tmp_path: Path):
    non_existent = tmp_path / "missing.docx"
    with pytest.raises(FileNotFoundError):
        extract_docx_text(non_existent)

    corrupt_file = tmp_path / "corrupt.docx"
    corrupt_file.write_bytes(b"not a zip file at all")
    with pytest.raises(ValueError):
        extract_docx_text(corrupt_file)
