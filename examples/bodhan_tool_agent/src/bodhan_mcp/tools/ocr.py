#!/usr/bin/env python3
from __future__ import annotations

import base64
import asyncio
import uuid
from pathlib import Path
from typing import Optional

from ..client import client
from ..models import ErrorCode
from ..media import inspect_pdf, inspect_image, pdf_to_pngs, detect_script


def _error(
    code: ErrorCode, message: str, retryable: bool = False,
    details: dict | None = None,
) -> dict:
    return {
        "error": code.value,
        "message": message,
        "code": code.value,
        "retryable": retryable,
        "details": details or {},
    }


def _write_temp_text_file(full_text: str, prefix: str = "ocr") -> tuple[str, str]:
    """Write full text to run/temp/ and return (file_path, preview)."""
    temp_dir = Path("run/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{prefix}_{uuid.uuid4().hex[:8]}.txt"
    file_path = temp_dir / file_name
    file_path.write_text(full_text, encoding="utf-8")

    # Create bounded preview (up to 2500 chars)
    max_preview = 2500
    if len(full_text) > max_preview:
        preview = full_text[:max_preview] + f"\n\n...[truncated, see {file_path}]"
    else:
        preview = full_text
    return file_path.as_posix(), preview


async def extract_document_text(
    file_path: str,
    page_range: Optional[str] = None,
) -> dict:
    path = Path(file_path)
    if not path.exists():
        return _error(
            ErrorCode.FILE_NOT_FOUND,
            f"File not found: {file_path}",
            retryable=False,
        )
    if path.is_dir():
        return _error(
            ErrorCode.INVALID_INPUT,
            f"Path is a directory, expected a document file: {file_path}",
            retryable=False,
        )

    suffix = path.suffix.lower()

    try:
        if suffix == ".pdf":
            info = inspect_pdf(str(path))
            total_pages = info.get("pages", 0)
            image_paths = pdf_to_pngs(str(path))
        elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
            info = inspect_image(str(path))
            total_pages = 1
            image_paths = [str(path)]
        elif suffix in (".txt", ".md"):
            # Text files: read directly without OCR
            full_text = path.read_text(encoding="utf-8", errors="ignore")
            script = detect_script(full_text) if full_text else "latin"
            text_file_path, preview = _write_temp_text_file(full_text, "text")
            assumptions = []
            if script != "latin":
                assumptions.append(f"Text file detected script: {script}")
            return {
                "markdown": f"<untrusted_source_data>{preview}</untrusted_source_data>",
                "text": full_text,
                "full_text_file": text_file_path,
                "pages": 1,
                "detected_script": script,
                "total_chars": len(full_text),
                "cost_inr": 0.0,
                "cached": False,
                "assumptions": assumptions,
            }
        else:
            return _error(
                ErrorCode.VALIDATION_ERROR,
                f"Unsupported file format for OCR: {suffix}",
                retryable=False,
                details={"supported": [
                    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff",
                    ".txt", ".md"
                ]},
            )
    except Exception as e:
        return _error(
            ErrorCode.MEDIA_PROCESSING_ERROR,
            f"Failed to inspect document: {e}",
            retryable=False,
        )

    start_page = 0
    end_page = total_pages
    if page_range:
        try:
            parts = page_range.split("-")
            start_int = int(parts[0])
            if start_int <= 0:
                return _error(
                    ErrorCode.INVALID_PAGE_RANGE,
                    f"page_range start must be >= 1, got '{parts[0]}'",
                    retryable=False,
                )
            start_page = start_int - 1
            if len(parts) > 1:
                end_int = int(parts[-1])
                if end_int <= 0:
                    return _error(
                        ErrorCode.INVALID_PAGE_RANGE,
                        f"page_range end must be >= 1, got '{parts[-1]}'",
                        retryable=False,
                    )
                if end_int < start_int:
                    return _error(
                        ErrorCode.INVALID_PAGE_RANGE,
                        f"page_range end ({end_int}) must be >= start ({start_int})",
                        retryable=False,
                    )
                end_page = min(total_pages, end_int)
            else:
                end_page = min(total_pages, start_int)
        except (ValueError, IndexError):
            return _error(
                ErrorCode.INVALID_PAGE_RANGE,
                f"Invalid page_range format: {page_range}. Use '1-3' or '5'.",
                retryable=False,
            )

    selected_images = image_paths[start_page:end_page]

    if not selected_images:
        return _error(
            ErrorCode.MEDIA_PROCESSING_ERROR,
            f"PDF has {total_pages} pages but no images were rendered",
            retryable=True,
            details={"total_pages": total_pages, "image_count": len(image_paths)},
        )

    semaphore = asyncio.Semaphore(3)

    async def process_page(idx: int, img_path: str) -> tuple[int, dict, float]:
        async with semaphore:
            try:
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
            except Exception as e:
                return idx, {"error": str(e), "cost": 0.0}

            try:
                api_resp = await client.request(
                    "indic-ocr",
                    "/chat/completions",
                    json_data={
                        "model": "indic-ocr",
                        "messages": [{
                            "role": "user",
                            "content": [{
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"}
                            }]
                        }],
                        "table_format": "markdown",
                    },
                    timeout=120,
                )
                resp = api_resp.data
                cost = api_resp.cost_inr
            except Exception as e:
                return idx, {"error": str(e), "cost": 0.0}

            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            blocks = resp.get("blocks", [])
            return idx, {"content": content, "blocks": blocks, "cost": cost}

    tasks = [process_page(i, p) for i, p in enumerate(selected_images)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_content = []
    all_blocks = []
    total_cost = 0.0
    errors = []

    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
            continue
        idx, page_data = result
        if "error" in page_data:
            errors.append(f"Page {idx}: {page_data['error']}")
        else:
            all_content.append(page_data["content"])
            all_blocks.extend(page_data["blocks"])
            total_cost += page_data.get("cost", 0.0)

    full_text = "\n\n".join(all_content)
    script = detect_script(full_text) if full_text else "latin"

    for img_path in image_paths:
        try:
            Path(img_path).unlink(missing_ok=True)
        except Exception:
            pass

    if not all_content and errors:
        return _error(
            ErrorCode.MEDIA_PROCESSING_ERROR,
            f"OCR failed for all pages: {'; '.join(errors)}",
            retryable=True,
            details={"errors": errors},
        )

    text_file_path, preview = _write_temp_text_file(full_text, "ocr")

    # Collect tool-level assumptions
    assumptions = []
    if script != "latin":
        assumptions.append(f"OCR detected script: {script}")

    return {
        "markdown": f"<untrusted_source_data>{preview}</untrusted_source_data>",
        "text": full_text,
        "full_text_file": text_file_path,
        "blocks": all_blocks,
        "pages": len(selected_images),
        "detected_script": script,
        "total_chars": len(full_text),
        "cost_inr": round(total_cost, 4),
        "cached": False,
        "errors": errors if errors else None,
        "assumptions": assumptions,
    }


__all__ = ["extract_document_text"]
