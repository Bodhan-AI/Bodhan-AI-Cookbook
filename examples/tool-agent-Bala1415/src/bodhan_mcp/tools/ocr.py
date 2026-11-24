"""
OCR tool — text extraction from images and scanned documents using Bodhan Indic-OCR.

Handles:
- Multi-page PDFs (converts to images, processes page by page)
- Single images (PNG, JPEG)
- Table preservation (markdown or HTML format)
- Reading-order Markdown output
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from bodhan_mcp.client import BodhanClient, BodhanAPIError
from bodhan_mcp.media import pdf_to_images, is_image, is_pdf, SUPPORTED_IMAGE_FORMATS

logger = logging.getLogger(__name__)


async def extract_text_ocr(
    client: BodhanClient,
    file_path: str,
    table_format: str = "markdown",
    output_dir: str | None = None,
) -> dict[str, Any]:
    """
    Extract text from an image or scanned PDF document.

    For PDFs, automatically converts each page to an image and processes
    them individually, then merges the results into a single Markdown document.

    Args:
        client: BodhanClient instance.
        file_path: Path to an image (PNG/JPEG) or PDF file.
        table_format: How to render tables — 'markdown' (default) or 'html'.
        output_dir: Directory for temporary page images. Uses system temp if None.

    Returns:
        dict with:
            - text: Full extracted text in Markdown format
            - pages: List of per-page results with text and confidence
            - num_pages: Total pages processed
            - avg_confidence: Average OCR confidence across all pages

    Raises:
        BodhanAPIError: If the API call fails.
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file format is unsupported.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Determine pages to process
    if is_pdf(file_path):
        logger.info(f"Converting PDF to images: {path.name}")
        page_images = pdf_to_images(str(path), output_dir=output_dir)
        logger.info(f"PDF has {len(page_images)} pages")
    elif is_image(file_path):
        page_images = [{"path": str(path.resolve()), "page_number": 1}]
    else:
        raise ValueError(
            f"Unsupported file format for OCR: {path.suffix}. "
            f"Supported: PDF, {', '.join(sorted(SUPPORTED_IMAGE_FORMATS))}"
        )

    # Process each page
    pages = []
    full_text_parts = []

    for page_info in page_images:
        logger.info(f"OCR page {page_info['page_number']}/{len(page_images)}")

        result = await client.ocr(
            image_path=page_info["path"],
            table_format=table_format,
        )

        page_result = {
            "page_number": page_info["page_number"],
            "text": result["text"],
            "confidence": result.get("confidence", 0.0),
            "blocks": result.get("blocks", []),
        }
        pages.append(page_result)

        # Add page header for multi-page documents
        if len(page_images) > 1:
            full_text_parts.append(f"<!-- Page {page_info['page_number']} -->\n{result['text']}")
        else:
            full_text_parts.append(result["text"])

    # Merge results
    full_text = "\n\n---\n\n".join(full_text_parts)

    # Average confidence
    confidences = [p["confidence"] for p in pages if p["confidence"] > 0]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return {
        "text": full_text,
        "pages": pages,
        "num_pages": len(pages),
        "avg_confidence": round(avg_conf, 3),
    }
