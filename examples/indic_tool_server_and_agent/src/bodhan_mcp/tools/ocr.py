"""Document OCR tool wrapping Indic-OCR."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, List

from bodhan_mcp.clients.ocr import OCRClient
from bodhan_mcp.config import settings
from bodhan_mcp.core.cache import cache_get, cache_set, file_content_hash
from bodhan_mcp.core.cost import calculate_ocr_cost
from bodhan_mcp.core.errors import BodhanError, FILE_NOT_FOUND, INVALID_INPUT, UNSUPPORTED_FORMAT
from bodhan_mcp.core.file_store import default_file_store
from bodhan_mcp.schemas import OCRDocumentOutput, OCRPageResult
from bodhan_mcp.tools.media import render_pdf_pages

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}


async def ocr_document(
    file_path: str,
    output_dir: Optional[str] = None,
) -> OCRDocumentOutput:
    """Run OCR on an image or PDF document using Indic-OCR.
    
    Extracts text from all Indic scripts and languages.
    PDFs are automatically rendered page-by-page.
    """
    t0 = time.time()
    p = Path(file_path)
    if not p.exists():
        raise BodhanError(FILE_NOT_FOUND, f"File not found: {file_path}")

    ext = p.suffix.lower()
    client = OCRClient()
    pages_results: List[OCRPageResult] = []
    text_pieces: List[str] = []

    if ext == ".pdf":
        render_res = render_pdf_pages(file_path, output_dir=output_dir)
        page_images = render_res.page_image_paths
        total_pages = len(page_images)
        for idx, img_path in enumerate(page_images, start=1):
            fhash = file_content_hash(img_path)
            cached_text = cache_get("indic-ocr", {"page": idx}, fhash)
            if cached_text:
                page_text = cached_text if isinstance(cached_text, str) else cached_text.get("text", "")
                is_cached = True
                p_cost = 0.0
            else:
                page_text = await client.ocr_image_file(img_path)
                cache_set("indic-ocr", {"page": idx}, fhash, page_text)
                is_cached = False
                p_cost = settings.ocr_price_per_image

            txt_file = default_file_store.save_text(page_text, prefix=f"ocr_p{idx}")
            pages_results.append(
                OCRPageResult(
                    page=idx,
                    text=page_text,
                    text_file_path=txt_file,
                    cost_inr=p_cost,
                    cached=is_cached,
                )
            )
            text_pieces.append(f"--- Page {idx} ---\n{page_text}")
    elif ext in SUPPORTED_IMAGE_EXTS:
        total_pages = 1
        fhash = file_content_hash(file_path)
        cached_text = cache_get("indic-ocr", {}, fhash)
        if cached_text:
            text = cached_text if isinstance(cached_text, str) else cached_text.get("text", "")
            is_cached = True
            cost = 0.0
        else:
            text = await client.ocr_image_file(file_path)
            cache_set("indic-ocr", {}, fhash, text)
            is_cached = False
            cost = calculate_ocr_cost(1)

        txt_file = default_file_store.save_text(text, prefix="ocr_single")
        pages_results.append(
            OCRPageResult(
                page=1,
                text=text,
                text_file_path=txt_file,
                cost_inr=cost,
                cached=is_cached,
            )
        )
        text_pieces.append(text)
    else:
        raise BodhanError(
            UNSUPPORTED_FORMAT,
            f"File format '{ext}' is not supported for OCR. Use PNG, JPG, JPEG, TIFF, BMP, or PDF.",
        )

    full_text = "\n\n".join(text_pieces)
    full_txt_file = default_file_store.save_text(full_text, prefix="ocr_full")
    total_cost = sum(p.cost_inr for p in pages_results)
    latency = round(time.time() - t0, 3)

    return OCRDocumentOutput(
        ok=True,
        file_path=str(p.resolve()),
        total_pages=total_pages,
        pages=pages_results,
        full_text=full_text,
        full_text_file_path=full_txt_file,
        total_cost_inr=round(total_cost, 4),
        latency_seconds=latency,
    )


# Alias as per assignment
extract_text_from_image = ocr_document
