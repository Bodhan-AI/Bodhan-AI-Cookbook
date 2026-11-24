"""
Extract text from images and scanned PDFs using Indic-OCR.

PDFs are rendered to per-page PNG images (200 DPI) with pdf2image/poppler,
then each page is sent to Indic-OCR concurrently. Low-confidence pages are
flagged rather than silently passed to translation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..clients.bodhan_client import call_ocr

_LOW_CONFIDENCE_THRESHOLD = 0.6
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}


async def ocr_image(image_path: Path) -> dict:
    """OCR a single image file.

    Returns:
        {"text": str, "confidence": float, "flagged": bool, "cost_inr": float}
    """
    result, cost = await call_ocr(Path(image_path))
    flagged = result["confidence"] < _LOW_CONFIDENCE_THRESHOLD
    return {
        "text": result["text"],
        "confidence": result["confidence"],
        "flagged": flagged,
        "cost_inr": cost,
    }


async def ocr_pdf(pdf_path: Path) -> dict:
    """OCR all pages of a scanned PDF.

    Renders pages to PNG using pypdfium2, then OCRs each page concurrently.

    Returns:
        {
          "pages": [{"page": int, "text": str, "confidence": float, "flagged": bool}],
          "full_text": str,
          "flagged_pages": [int],
          "cost_inr": float,
        }
    """
    import pypdfium2 as pdfium

    pdf_path = Path(pdf_path)
    img_dir = pdf_path.parent / f"ocr_pages_{pdf_path.stem}"
    img_dir.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(pdf_path))
    image_paths: list[Path] = []
    for i, page in enumerate(pdf):
        p = img_dir / f"page_{i + 1:04d}.png"
        pil_img = page.render(scale=2).to_pil()
        pil_img.save(str(p), "PNG")
        image_paths.append(p)

    raw_results = await asyncio.gather(*[call_ocr(p) for p in image_paths])

    pages: list[dict] = []
    total_cost = 0.0
    flagged_pages: list[int] = []

    for i, (result, cost) in enumerate(raw_results):
        page_no = i + 1
        flagged = result["confidence"] < _LOW_CONFIDENCE_THRESHOLD
        if flagged:
            flagged_pages.append(page_no)
        pages.append(
            {
                "page": page_no,
                "text": result["text"],
                "confidence": result["confidence"],
                "flagged": flagged,
            }
        )
        total_cost += cost

    full_text = "\n\n---PAGE BREAK---\n\n".join(p["text"] for p in pages)

    return {
        "pages": pages,
        "full_text": full_text,
        "flagged_pages": flagged_pages,
        "cost_inr": round(total_cost, 4),
    }


async def ocr_file(file_path: Path) -> dict:
    """OCR an image or PDF. Dispatches to the correct handler by file extension.

    Returns the same shape as ocr_pdf for PDFs, or a single-page equivalent
    for images (pages list with one entry).
    """
    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        return await ocr_pdf(path)

    result = await ocr_image(path)
    return {
        "pages": [{"page": 1, **result}],
        "full_text": result["text"],
        "flagged_pages": [1] if result["flagged"] else [],
        "cost_inr": result["cost_inr"],
    }
