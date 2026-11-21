"""Document Processing and PDF/Multi-Format Document Rasterization & DOCX Extraction.

Provides:
- Multi-format document rasterization (PDF, XPS, EPUB, MOBI, CBZ, multi-page TIFF) using PyMuPDF (fitz)
- Zero-dependency DOCX text and table extraction using standard library zipfile and xml.etree.ElementTree
- Document metadata inspection for both PyMuPDF-backed formats and DOCX
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

import fitz  # PyMuPDF

# Document formats supported by PyMuPDF rasterization
SUPPORTED_DOCUMENT_EXTENSIONS: set[str] = {
    ".pdf",
    ".xps",
    ".oxps",
    ".epub",
    ".mobi",
    ".fb2",
    ".cbz",
    ".tiff",
    ".tif",
}

# Unicode code point ranges for Indic scripts (Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam)
INDIC_UNICODE_RANGES: tuple[int, int] = (0x0900, 0x0D7F)


def is_valid_indic_or_latin_char(c: str) -> bool:
    """Check if character is standard ASCII alphanumeric or inside Indic Unicode blocks."""
    code = ord(c)
    if (c.isascii() and c.isalnum()) or c in " \t\n\r,.-;:!?\"'()[]{}":
        return True
    return INDIC_UNICODE_RANGES[0] <= code <= INDIC_UNICODE_RANGES[1]


def evaluate_page_digital_text(page: fitz.Page, min_chars: int = 50) -> tuple[bool, str]:
    """Multi-signal evaluation: Determines if a PDF page contains true digital text vs scanned/legacy mojibake.

    Checks:
    1. Digital character count threshold.
    2. Image coverage area ratio (if a single full-page image covers >= 75% of page bounding area, treated as scan).
    3. Character block validation (filters out Kruti-Dev / legacy font mojibake where ASCII characters masquerade as Indic glyphs).

    Returns:
        tuple[bool, str]: (is_digital, extracted_text)
    """
    raw_text = page.get_text().strip()
    if len(raw_text) < min_chars:
        return False, ""

    # Signal 2: Check full-page image coverage ratio
    try:
        page_area = abs(page.rect)
        images = page.get_images(full=True)
        if images and page_area > 0:
            total_img_area = 0.0
            for img_info in images:
                xref = img_info[0]
                for r in page.get_image_rects(xref):
                    total_img_area += abs(r)
            # If an image covers >= 75% of page and text is under 200 chars (likely watermark/header), it's a scan
            if (total_img_area / page_area) >= 0.75 and len(raw_text) < 200:
                return False, ""
    except Exception:
        pass

    # Signal 3: Character block coherence (Indic Unicode + Latin alphanumeric vs mojibake noise)
    valid_count = sum(1 for c in raw_text if is_valid_indic_or_latin_char(c))
    if (valid_count / max(len(raw_text), 1)) < 0.65:
        # High ratio of control/symbol/garbled glyphs -> legacy encoding or scan artifact
        return False, ""

    return True, raw_text


def rasterize_specific_pages(
    doc_path: str | Path,
    output_dir: str | Path,
    page_numbers: list[int],
    dpi: int = 150,
) -> list[tuple[int, Path]]:
    """Rasterize only specific 1-based page numbers from a document for targeted OCR.

    Args:
        doc_path: Path to the document.
        output_dir: Target output directory for PNGs.
        page_numbers: List of 1-based page indices to rasterize (e.g. [2, 5]).
        dpi: Resolution in dots-per-inch (default 150).

    Returns:
        List of tuples: (1-based page_num, Path to rendered PNG).
    """
    path = Path(doc_path).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        raise FileNotFoundError(f"Document file does not exist: {path}")

    doc = fitz.open(str(path))
    try:
        rendered: list[tuple[int, Path]] = []
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        for p_num in page_numbers:
            if 1 <= p_num <= len(doc):
                page = doc.load_page(p_num - 1)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                out_image = out_dir / f"page_{p_num:03d}.png"
                pix.save(str(out_image))
                rendered.append((p_num, out_image))

        return rendered
    finally:
        doc.close()

# Image formats supported for OCR & vision
SUPPORTED_IMAGE_EXTENSIONS: set[str] = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tiff",
    ".tif",
}

# OpenXML namespace for WordprocessingML
_WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def rasterize_document_pages(
    doc_path: str | Path,
    output_dir: str | Path,
    dpi: int = 150,
    page_limit: int | None = None,
) -> list[Path]:
    """Rasterize pages of a document (PDF, XPS, EPUB, MOBI, TIFF, etc.) into PNG images on disk for Indic-OCR.

    PyMuPDF naturally opens PDF, XPS, EPUB, MOBI, CBZ, and multi-page TIFF without external Poppler.

    Args:
        doc_path: Absolute or relative path to the source document.
        output_dir: Target directory where rendered PNGs will be stored.
        dpi: Resolution in dots-per-inch (150 DPI provides optimal OCR accuracy vs. token budget).
        page_limit: Maximum number of pages to rasterize (None for all).

    Returns:
        List of Paths to the rasterized PNG images on disk.
    """
    path = Path(doc_path).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        raise FileNotFoundError(f"Document file does not exist: {path}")

    doc = fitz.open(str(path))
    try:
        image_paths: list[Path] = []
        zoom = dpi / 72.0  # 72 is PyMuPDF default point size
        matrix = fitz.Matrix(zoom, zoom)

        num_pages = len(doc)
        if page_limit is not None and page_limit > 0:
            num_pages = min(num_pages, page_limit)

        for page_num in range(num_pages):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out_image = out_dir / f"page_{page_num + 1:03d}.png"
            pix.save(str(out_image))
            image_paths.append(out_image)

        return image_paths
    finally:
        doc.close()


def rasterize_pdf_pages(
    pdf_path: str | Path,
    output_dir: str | Path,
    dpi: int = 150,
    page_limit: int | None = None,
) -> list[Path]:
    """Rasterize pages of a PDF into PNG images on disk for Indic-OCR.

    Maintained for backward compatibility; calls rasterize_document_pages.
    """
    return rasterize_document_pages(pdf_path, output_dir, dpi=dpi, page_limit=page_limit)


def extract_docx_text(
    docx_path: str | Path,
    table_format: str = "markdown",
) -> str:
    """Extract paragraphs and tables from a .docx file using only standard library zipfile and xml.etree.

    Zero external dependencies (no python-docx required).

    Args:
        docx_path: Path to the .docx document on disk.
        table_format: Format for extracted tables ('markdown' or 'html').

    Returns:
        Extracted text content with paragraphs and structured tables separated by blank lines.
    """
    path = Path(docx_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"DOCX file does not exist: {path}")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                raise ValueError(f"Invalid DOCX archive (missing word/document.xml): {path.name}")
            xml_bytes = zf.read("word/document.xml")
    except zipfile.BadZipFile as e:
        raise ValueError(f"Corrupted or invalid DOCX archive {path.name}: {e}") from e

    root = ET.fromstring(xml_bytes)
    body = root.find("w:body", _WORD_NS)
    if body is None:
        return ""

    blocks: list[str] = []

    def _extract_para_text(p_node: ET.Element) -> str:
        fragments: list[str] = []
        for child in p_node.iter():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "t" and child.text:
                fragments.append(child.text)
            elif tag == "tab":
                fragments.append("\t")
            elif tag in {"br", "cr"}:
                fragments.append("\n")
        return "".join(fragments).strip()

    for elem in body:
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        if tag == "p":
            para_text = _extract_para_text(elem)
            if para_text:
                blocks.append(para_text)

        elif tag == "tbl":
            # Extract table rows and cells
            table_rows: list[list[str]] = []
            for tr in elem.findall("w:tr", _WORD_NS):
                row_cells: list[str] = []
                for tc in tr.findall("w:tc", _WORD_NS):
                    cell_paras: list[str] = []
                    for p in tc.findall("w:p", _WORD_NS):
                        txt = _extract_para_text(p)
                        if txt:
                            cell_paras.append(txt)
                    row_cells.append(" ".join(cell_paras).strip())
                if row_cells:
                    table_rows.append(row_cells)

            if not table_rows:
                continue

            if table_format.lower() == "html":
                html_lines = ["<table>"]
                for r_idx, row in enumerate(table_rows):
                    tag_cell = "th" if r_idx == 0 else "td"
                    cells_html = "".join(f"<{tag_cell}>{c}</{tag_cell}>" for c in row)
                    html_lines.append(f"  <tr>{cells_html}</tr>")
                html_lines.append("</table>")
                blocks.append("\n".join(html_lines))
            else:
                # Default to Markdown table
                # Normalize column width
                max_cols = max(len(r) for r in table_rows)
                norm_rows = [r + [""] * (max_cols - len(r)) for r in table_rows]

                header = "| " + " | ".join(norm_rows[0]) + " |"
                sep = "| " + " | ".join(["---"] * max_cols) + " |"
                data_rows = ["| " + " | ".join(r) + " |" for r in norm_rows[1:]]
                blocks.append("\n".join([header, sep] + data_rows))

    return "\n\n".join(blocks).strip()


def get_document_metadata(doc_path: str | Path) -> dict[str, Any]:
    """Retrieve metadata (page count, format, title, etc.) from any supported document or docx.

    Args:
        doc_path: Path to the document.

    Returns:
        dict with metadata keys: page_count (or paragraph_count for docx), format, file, etc.
    """
    path = Path(doc_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Document file does not exist: {path}")

    suffix = path.suffix.lower()

    if suffix == ".docx":
        # Zero-dependency docx metadata
        try:
            with zipfile.ZipFile(path, "r") as zf:
                if "word/document.xml" in zf.namelist():
                    xml_bytes = zf.read("word/document.xml")
                    root = ET.fromstring(xml_bytes)
                    body = root.find("w:body", _WORD_NS)
                    p_count = len(body.findall("w:p", _WORD_NS)) if body is not None else 0
                    tbl_count = len(body.findall("w:tbl", _WORD_NS)) if body is not None else 0
                    return {
                        "type": "document",
                        "format": "DOCX",
                        "file": path.name,
                        "paragraph_count": p_count,
                        "table_count": tbl_count,
                        "page_count": 1,
                        "is_encrypted": False,
                    }
        except Exception:
            pass
        return {
            "type": "document",
            "format": "DOCX",
            "file": path.name,
            "page_count": 1,
            "is_encrypted": False,
        }

    doc = fitz.open(str(path))
    try:
        fmt = doc.metadata.get("format") if doc.metadata else None
        if not fmt:
            fmt = suffix.lstrip(".").upper()
        meta = {
            "type": "pdf" if suffix == ".pdf" else "document",
            "page_count": len(doc),
            "is_encrypted": doc.is_encrypted,
            "format": fmt,
            "title": (doc.metadata or {}).get("title", ""),
            "file": path.name,
        }
        return meta
    finally:
        doc.close()


def get_pdf_metadata(pdf_path: str | Path) -> dict[str, Any]:
    """Retrieve page count and metadata from a PDF file.

    Maintained for backward compatibility; calls get_document_metadata.
    """
    return get_document_metadata(pdf_path)
