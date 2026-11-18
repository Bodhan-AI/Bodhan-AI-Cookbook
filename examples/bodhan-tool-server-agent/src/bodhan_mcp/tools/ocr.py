"""Indic-OCR Tool for Bodhan MCP.

Performs document digitization and OCR for Indian language documents and images.
Extracts reading-ordered Markdown from multi-page PDFs and images using Bodhan Indic-OCR.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from bodhan_mcp.cache import cache
from bodhan_mcp.clients.bodhan_client import bodhan_client
from bodhan_mcp.config import config
from bodhan_mcp.media_utils import (
    DOC_EXTENSIONS,
    IMAGE_EXTENSIONS,
    extract_pdf_pages_to_images,
    image_file_to_base64_data_url,
    inspect_pdf,
    validate_file_reference,
)

logger = logging.getLogger("bodhan.tools.ocr")


async def extract_document_text(
    file_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Extract printed/handwritten text and tables from an image or multi-page PDF using Indic-OCR.
    
    Args:
        file_path: Local path to an image (.png, .jpg, .jpeg) or PDF (.pdf) file.
        output_path: Optional path to save the extracted Markdown file.
        
    Returns:
        Structured dictionary with extracted Markdown text, page count, and INR cost.
    """
    path = validate_file_reference(
        file_path,
        allowed_extensions=IMAGE_EXTENSIONS.union({".pdf"}),
        must_exist=True,
    )

    # Check cache
    params = {"tool": "ocr", "file_name": path.name}
    cache_key = cache.generate_key("ocr", params, path)
    cached_result = cache.get(cache_key)
    if cached_result:
        cached_result["cached"] = True
        cached_result["cost_inr"] = 0.0
        return cached_result

    ext = path.suffix.lower()
    page_texts: List[str] = []
    page_count = 1

    if ext == ".pdf":
        pdf_info = inspect_pdf(path)
        page_count = max(1, pdf_info.get("page_count", 1))
        pages_info = extract_pdf_pages_to_images(path)

        for p_info in pages_info:
            p_num = p_info["page_number"]
            # If an image was extracted for this page, run OCR on it
            if "image_path" in p_info and Path(p_info["image_path"]).exists():
                data_url = image_file_to_base64_data_url(p_info["image_path"])
                ocr_res = await bodhan_client.ocr(data_url)
                text = ocr_res.get("text", "").strip()
            else:
                # Fallback to direct OCR mock or text extract
                text = p_info.get("text_extract", "").strip() or f"Content of Page {p_num}"

            page_texts.append(f"## Page {p_num}\n\n{text}")
    else:
        # Single image file
        data_url = image_file_to_base64_data_url(path)
        ocr_res = await bodhan_client.ocr(data_url)
        page_texts.append(ocr_res.get("text", ""))

    unified_markdown = "\n\n---\n\n".join(page_texts)
    actual_cost = config.calculate_ocr_cost(page_count)

    # If output_path requested, save markdown
    saved_path = None
    if output_path:
        out_p = Path(output_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            f.write(unified_markdown)
        saved_path = str(out_p)

    result = {
        "text": unified_markdown,
        "page_count": page_count,
        "file_format": ext.lstrip("."),
        "model": "indic-ocr",
        "cost_inr": actual_cost,
        "output_path": saved_path,
        "cached": False,
    }

    cache.set(cache_key, result)
    return result
