"""
ocr_document tool — document text extraction via Bodhan Indic-OCR.

Accepts images (PNG, JPEG, WebP) and multi-page PDF documents.
Renders PDF pages to images and processes them page by page.
Preserves Markdown tables.
Cost: ₹0.20 per image/page.
"""
from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Literal

from bodhan_core.cost import estimate_ocr
from bodhan_core.errors import BodhanError, ErrorCode, error_response, success_response
from bodhan_core.media.image import validate_image
from bodhan_core.media.pdf import render_pdf_pages

log = logging.getLogger(__name__)


async def run_ocr(
    file_path: str,
    table_format: Literal["markdown", "html"] = "markdown",
    *,
    client,
    workspace,
    cost_meter,
) -> dict:
    t0 = time.monotonic()
    try:
        try:
            resolved = workspace.resolve(file_path)
        except BodhanError as exc:
            return exc.to_response()
        except Exception as exc:  # noqa: BLE001
            return error_response(
                ErrorCode.FILE_NOT_FOUND,
                f"Cannot resolve path: {exc}",
                "Check the file path.",
            )

        if not resolved.exists():
            return error_response(
                ErrorCode.FILE_NOT_FOUND,
                f"File not found: {file_path}",
                "Provide a path to an existing image or PDF file.",
            )

        suffix = resolved.suffix.lower()
        image_paths: list[Path] = []
        is_pdf = suffix == ".pdf"

        # 1. Prepare images (render PDF if needed)
        temp_dir_obj = None
        if is_pdf:
            temp_dir_obj = tempfile.TemporaryDirectory()
            temp_dir = Path(temp_dir_obj.name)
            try:
                image_paths = render_pdf_pages(resolved, temp_dir)
            except BodhanError as exc:
                return exc.to_response()
            except Exception as exc:
                return error_response(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Failed to process PDF: {exc}",
                    "Ensure the PDF file is valid and readable.",
                )
        else:
            try:
                validate_image(resolved)
                image_paths = [resolved]
            except BodhanError as exc:
                return exc.to_response()

        total_pages = len(image_paths)
        if total_pages == 0:
            return error_response(
                ErrorCode.UNSUPPORTED_FORMAT,
                "No readable pages found in input document.",
                "Provide a non-empty image or PDF.",
            )

        # 2. Check budget
        estimated_cost = estimate_ocr(total_pages)
        if not cost_meter.check_budget(estimated_cost, remaining=float("inf")):
            if temp_dir_obj:
                temp_dir_obj.cleanup()
            return error_response(
                ErrorCode.BUDGET_EXCEEDED,
                f"Estimated OCR cost (₹{estimated_cost:.2f}) exceeds configured budget.",
                "Raise the budget or reduce the number of pages.",
            )

        # 3. Perform OCR page by page
        page_results: list[dict] = []
        pages_succeeded = 0
        pages_failed = 0
        actual_cost = 0.0

        for page_idx, img_p in enumerate(image_paths):
            try:
                res = await client.ocr(img_p, table_format=table_format)
                page_results.append({
                    "page": page_idx + 1,
                    "markdown": res.get("markdown", ""),
                    "blocks": res.get("blocks", []),
                })
                pages_succeeded += 1
                actual_cost += estimate_ocr(1)
            except Exception as exc:
                log.error("OCR failed on page %d: %s", page_idx + 1, exc)
                pages_failed += 1
                page_results.append({
                    "page": page_idx + 1,
                    "error": str(exc),
                    "markdown": f"<!-- Page {page_idx + 1} OCR extraction failed: {exc} -->",
                })

        if temp_dir_obj:
            temp_dir_obj.cleanup()

        # 4. Combine Markdown
        combined_md_parts: list[str] = []
        for p in page_results:
            if total_pages > 1:
                combined_md_parts.append(f"<!-- Page {p['page']} -->\n" + p.get("markdown", ""))
            else:
                combined_md_parts.append(p.get("markdown", ""))

        full_markdown = "\n\n".join(combined_md_parts)

        # 5. Save output file
        stem = resolved.stem
        out_path = workspace.temp_path(f"{stem}.ocr.md")
        out_path.write_text(full_markdown, encoding="utf-8")

        duration_ms = int((time.monotonic() - t0) * 1000)
        return success_response(
            {
                "output_file": str(out_path),
                "page_count": total_pages,
                "pages_succeeded": pages_succeeded,
                "pages_failed": pages_failed,
                "full_text_preview": full_markdown[:500],
                "markdown": full_markdown,
            },
            cost_inr=actual_cost,
            cached=False,
            duration_ms=duration_ms,
        )

    except BodhanError as exc:
        return exc.to_response()
    except Exception as exc:  # noqa: BLE001
        log.error("ocr_document error: %s", exc, exc_info=True)
        return error_response(
            ErrorCode.UPSTREAM_ERROR,
            f"Unexpected error during OCR: {exc}",
            "Check server logs and retry.",
            retryable=True,
        )


def register(mcp, client, workspace, cost_meter) -> None:  # noqa: ANN001
    @mcp.tool()
    async def ocr_document(
        file_path: str,
        table_format: Literal["markdown", "html"] = "markdown",
    ) -> dict:
        """Extract text from an image or scanned PDF using Bodhan Indic-OCR. \
Accepts PNG, JPEG, or PDF files. PDF pages are rendered to images automatically. \
Returns Markdown text with tables preserved. For multi-page PDFs, returns per-page \
results. Returns FILE_NOT_FOUND if the file is missing, UNSUPPORTED_FORMAT if \
the file type is not supported. Cost: ₹0.20 per image/page."""
        return await run_ocr(
            file_path=file_path,
            table_format=table_format,
            client=client,
            workspace=workspace,
            cost_meter=cost_meter,
        )
