"""MCP Tool: bodhan_ocr
Document and Image OCR via Indic-OCR.
Uses PyMuPDF (fitz) for PDF page extraction and rasterization (no Poppler).
"""

import base64
import io
import os
import tempfile
from typing import List, Optional
import pymupdf  # PyMuPDF
from PIL import Image
from bodhan_mcp.client import bodhan_client
from bodhan_mcp.cost import calculate_ocr_cost
from bodhan_mcp.types import OCROutput, ToolError


def extract_pages_from_pdf(pdf_path: str, page_numbers: Optional[List[int]] = None) -> List[Image.Image]:
    """Render PDF pages to PIL Images using PyMuPDF only."""
    doc = pymupdf.open(pdf_path)
    images: List[Image.Image] = []

    total_pages = len(doc)
    target_pages = page_numbers if page_numbers else list(range(1, total_pages + 1))

    for p_num in target_pages:
        if 1 <= p_num <= total_pages:
            page = doc[p_num - 1]
            rect = page.rect
            scale = 1.0 if max(rect.width, rect.height) >= 1000 else 1.5
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            images.append(img)

    doc.close()
    return images


def run_ocr(
    file_path: str,
    page_numbers: Optional[List[int]] = None,
) -> OCROutput:
    """Run OCR on image or multi-page PDF document."""
    if not os.path.exists(file_path):
        raise ToolError("FILE_NOT_FOUND", f"Document not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    pil_images: List[Image.Image] = []

    if ext == ".pdf":
        pil_images = extract_pages_from_pdf(file_path, page_numbers)
    elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        pil_images = [Image.open(file_path)]
    else:
        raise ToolError("UNSUPPORTED_FORMAT", f"Unsupported document format: {ext}")

    page_texts: List[str] = []
    total_tables = 0
    all_cached = True

    for p_idx, img in enumerate(pil_images, 1):
        # Resize to max 1200px width/height to keep payload light (<150KB) while preserving high OCR accuracy
        w, h = img.size
        max_dim = max(w, h)
        if max_dim > 1200:
            ratio = 1200.0 / max_dim
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        # Ensure RGB mode for JPEG encoding
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Convert PIL Image to Base64 (JPEG format, quality 85 keeps size ~80-150KB to avoid network timeouts)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()

        text, tables, is_cached = bodhan_client.ocr(b64, mime_type="image/jpeg")
        if not is_cached:
            all_cached = False

        total_tables += tables
        if len(pil_images) > 1:
            page_texts.append(f"<!-- Page {p_idx} -->\n{text}")
        else:
            page_texts.append(text)

    full_text = "\n\n".join(page_texts).strip()
    page_count = len(pil_images)
    cost_inr = 0.0 if all_cached else calculate_ocr_cost(page_count)

    return OCROutput(
        text=full_text,
        page_count=page_count,
        tables_detected=total_tables,
        cost_inr=cost_inr,
        cached=all_cached,
    )

