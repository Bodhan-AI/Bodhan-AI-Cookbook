"""Bodhan Document OCR tool using indic-ocr."""

from __future__ import annotations

import base64
import mimetypes
import os
import time
from pathlib import Path
from typing import Any
import httpx

from bodhan_mcp.config import (
    BODHAN_API_KEY,
    BODHAN_BASE_URL,
    BODHAN_OCR_API_KEY,
    MODEL_OCR,
    OCR_TIMEOUT_SECONDS,
    PRICING,
)
from bodhan_mcp.errors import (
    BodhanAPIError,
    BodhanAuthenticationError,
    BodhanRateLimitError,
    InvalidInputError,
)
from bodhan_mcp.schemas import ExtractTextFromDocumentOutput, PageOCROutput
from bodhan_mcp.tools.file_utils import ALLOWED_DOC_EXTENSIONS, resolve_safe_path
from bodhan_mcp.tools.media import rasterize_pdf_to_images


def _call_ocr_page_api(
    client: httpx.Client,
    image_path: Path,
    table_format: str,
    api_key: str,
    base_url: str,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Call POST /v1/chat/completions with indic-ocr model."""
    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    b64_str = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    data_url = f"data:{mime};base64,{b64_str}"

    body = {
        "model": MODEL_OCR,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    }
                ],
            }
        ],
        "table_format": table_format,
    }

    backoff = 1.0
    for attempt in range(max_retries):
        try:
            resp = client.post(url, headers=headers, json=body, timeout=OCR_TIMEOUT_SECONDS)
            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 401:
                raise BodhanAuthenticationError("Invalid or missing BODHAN_API_KEY")

            if resp.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise BodhanRateLimitError("Rate limit exceeded for document OCR")

            if resp.status_code >= 500:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise BodhanAPIError(resp.status_code, f"Server error from Bodhan API: {resp.text}")

            raise BodhanAPIError(resp.status_code, f"OCR API failed: {resp.text}")

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2.0
                continue
            raise BodhanAPIError(504, f"Network timeout during OCR: {e}")

    return {}


def extract_text_from_document(
    file_path: str,
    table_format: str = "markdown",
    api_key: str | None = None,
    base_url: str | None = None,
) -> ExtractTextFromDocumentOutput:
    """Extract text and layout from an image or multi-page PDF using Bodhan indic-ocr.

    Converts PDFs into individual page images, calls indic-ocr per page, preserves document
    reading order and tables, and outputs aggregated Markdown text.
    """
    key = api_key or os.getenv("BODHAN_OCR_API_KEY") or os.getenv("BODHAN_API_KEY") or BODHAN_OCR_API_KEY or BODHAN_API_KEY
    if not key:
        raise BodhanAuthenticationError("BODHAN_API_KEY is not set")
    api_root = (base_url or os.getenv("BODHAN_BASE_URL") or BODHAN_BASE_URL).rstrip("/")

    resolved_path = resolve_safe_path(file_path, allowed_extensions=ALLOWED_DOC_EXTENSIONS)

    if table_format not in {"markdown", "html"}:
        raise InvalidInputError(
            f"Invalid table_format '{table_format}'. Must be 'markdown' or 'html'.",
            field="table_format",
        )

    # Determine pages to process
    page_images: list[tuple[int, Path]] = []
    is_pdf = resolved_path.suffix.lower() == ".pdf"

    if is_pdf:
        page_images = rasterize_pdf_to_images(resolved_path)
    else:
        page_images = [(1, resolved_path)]

    if not page_images:
        raise InvalidInputError(f"No readable pages found in {resolved_path}")

    page_results: list[PageOCROutput] = []
    successful_pages = 0
    failed_pages = 0
    full_markdown_sections: list[str] = []

    with httpx.Client() as client:
        for page_num, img_path in page_images:
            try:
                res = _call_ocr_page_api(
                    client=client,
                    image_path=img_path,
                    table_format=table_format,
                    api_key=key,
                    base_url=api_root,
                )
                choices = res.get("choices", [])
                content = choices[0]["message"]["content"] if choices else ""
                blocks = res.get("blocks", [])

                page_out = PageOCROutput(
                    page_number=page_num,
                    text=content,
                    blocks_count=len(blocks),
                    blocks=blocks,
                    success=True,
                )
                page_results.append(page_out)
                successful_pages += 1
                if content.strip():
                    full_markdown_sections.append(f"<!-- Page {page_num} -->\n{content}")

            except Exception as e:
                failed_pages += 1
                page_results.append(
                    PageOCROutput(
                        page_number=page_num,
                        text="",
                        blocks_count=0,
                        blocks=[],
                        success=False,
                        error=str(e),
                    )
                )
            finally:
                # If we generated a temp page image from PDF, clean it up
                if is_pdf and img_path.exists():
                    try:
                        img_path.unlink()
                    except Exception:
                        pass

    # Clean up temp directory if PDF
    if is_pdf and page_images:
        temp_parent = page_images[0][1].parent
        if temp_parent.exists():
            try:
                temp_parent.rmdir()
            except Exception:
                pass

    total_cost = round(len(page_images) * PRICING.ocr_per_page_inr, 4)
    complete_text = "\n\n".join(full_markdown_sections).strip()

    overall_success = successful_pages > 0

    return ExtractTextFromDocumentOutput(
        success=overall_success,
        text=complete_text,
        total_pages=len(page_images),
        successful_pages=successful_pages,
        failed_pages=failed_pages,
        pages=page_results,
        estimated_cost_inr=total_cost,
        error=None if failed_pages == 0 else f"{failed_pages} out of {len(page_images)} pages failed OCR",
    )
