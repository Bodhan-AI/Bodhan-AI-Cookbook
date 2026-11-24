"""
PDF Processor — Handles both digital (text-based) and scanned (image-based) PDFs.
Uses PyMuPDF (fitz) to extract text or render pages to images for OCR.
"""

import fitz  # PyMuPDF
from typing import List, Dict, Any, Tuple
from bodhan_api import translate, scan_and_translate, BodhanAPIError


def get_pdf_info(pdf_bytes: bytes) -> Tuple[int, List[str]]:
    """Return total page count and page summary info."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    num_pages = len(doc)
    doc.close()
    return num_pages


def process_pdf_page(
    doc: fitz.Document,
    page_num: int,
    target_language: str,
    ocr_key: str,
    translate_key: str,
    script: str = "native",
) -> Dict[str, Any]:
    """
    Process a single PDF page (0-indexed).
    1. If extractable digital text is present (>40 chars) -> Use direct Translation.
    2. Otherwise (scanned/image page) -> Render page to PNG and run OCR + Translation.

    Returns dict with keys:
        page_number: int (1-based)
        method: 'digital' or 'scanned_ocr'
        original_text: str
        translated_text: str
        image_bytes: bytes (preview)
        chapter: Optional[str]
    """
    page = doc.load_page(page_num)
    digital_text = page.get_text().strip()

    # Always generate a clean page thumbnail image for UI preview
    pix = page.get_pixmap(dpi=150)
    page_img_bytes = pix.tobytes("png")

    # Heuristic: if text length > 40 chars, it's a digital text page
    if len(digital_text) > 40:
        # ── Digital Text Mode: Direct Translation ──
        # Split text into paragraphs or chunks (keep within ~1500 chars for translate)
        paragraphs = [p.strip() for p in digital_text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [digital_text]

        translated_paragraphs = []
        for p in paragraphs:
            # Chunk long paragraphs if necessary
            chunk = p[:1500]
            try:
                res = translate(
                    api_key=translate_key,
                    text=chunk,
                    target_language=target_language,
                    script=script if script != "native" else None,
                )
                translated_paragraphs.append(res.translation)
            except Exception as e:
                translated_paragraphs.append(f"[Translation error: {e}]")

        full_translation = "\n\n".join(translated_paragraphs)

        return {
            "page_number": page_num + 1,
            "method": "digital_translation",
            "original_text": digital_text,
            "translated_text": full_translation,
            "image_bytes": page_img_bytes,
            "chapter": None,
        }

    else:
        # ── Scanned Image Mode: OCR + Translation ──
        ocr_res = scan_and_translate(
            ocr_key=ocr_key,
            translate_key=translate_key,
            image_bytes=page_img_bytes,
            target_language=target_language,
            image_format="png",
            script=script if script != "native" else None,
            table_format="markdown",
        )

        chapter = None
        for tb in ocr_res["translated_blocks"]:
            if getattr(tb["block"], "label", "") in ("Chapter-title", "Title"):
                chapter = tb["translation"]
                break

        return {
            "page_number": page_num + 1,
            "method": "scanned_ocr",
            "original_text": ocr_res["ocr_result"].content,
            "translated_text": ocr_res["translated_text"],
            "image_bytes": page_img_bytes,
            "chapter": chapter,
        }

