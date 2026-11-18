"""Indic-OCR tool for Bodhan MCP Server.

Wraps Bodhan AI's Indic-OCR model (POST https://api.bodhan.ai/v1/chat/completions)
with PyMuPDF document rendering, local per-page disk caching, strict pre-flight
validation, page capping, prompt injection defense, and structured error reporting.
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
import pymupdf
from pydantic import BaseModel, Field

from bodhan_mcp.common import (
    BODHAN_API_BASE,
    CallTracker,
    PRICE_OCR_PER_PAGE_INR,
    StructuredError,
    calculate_ocr_cost_inr,
    classify_http_response,
    get_cache_key,
    read_cache,
    write_cache,
    check_live_calls_allowed,
    check_global_cap_allowed,
    is_mock_transport,
    log_spend_dispatched,
    update_spend_ledger,
    log_spend_cache_hit,
)

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------

MODEL_NAME = "indic-ocr"
CACHE_DIR = Path(".cache/bodhan_ocr")
DEFAULT_MAX_PAGES_PER_CALL = 10  # Safe cap per tool call
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | {".pdf"}


# ------------------------------------------------------------------------------
# Pydantic Schemas
# ------------------------------------------------------------------------------


class PageResult(BaseModel):
    page_number: int = Field(..., description="1-indexed page number.")
    text: str = Field("", description="Extracted Markdown/LaTeX text for this page.")
    cache_hit: bool = Field(
        False, description="Whether this page was served from local disk cache."
    )
    error: Optional[StructuredError] = Field(None, description="Structured error if page failed.")


class OcrInput(BaseModel):
    file_path: str = Field(
        ...,
        description="Path to the document file (PNG, JPG, JPEG, WEBP, or PDF). Never pass inline base64.",
    )
    max_pages: int = Field(
        DEFAULT_MAX_PAGES_PER_CALL,
        description=f"Maximum pages to process for PDF documents (default {DEFAULT_MAX_PAGES_PER_CALL}).",
    )


class OcrOutput(BaseModel):
    success: bool
    file_path: str
    total_pages_in_doc: int = 1
    pages_processed: int = 1
    capped: bool = False
    pages: List[PageResult] = []
    full_text: str = ""
    cached_pages_count: int = 0
    billed_pages_count: int = 0
    estimated_cost_inr: float = 0.0
    error: Optional[StructuredError] = None


# ------------------------------------------------------------------------------
# Tool Implementation
# ------------------------------------------------------------------------------


class IndicOcrTool:
    """Manages document OCR calls to Bodhan AI's indic-ocr model."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BODHAN_API_BASE,
        cache_dir: Path = CACHE_DIR,
        client: Optional[httpx.AsyncClient] = None,
        max_concurrency: int = 3,
        default_max_pages: int = DEFAULT_MAX_PAGES_PER_CALL,
        run_id: Optional[str] = None,
        ledger_path: Optional[Union[str, Path]] = None,
    ):
        self.api_key = api_key or os.getenv("BODHAN_OCR_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.custom_client = client
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.default_max_pages = default_max_pages
        self.run_id = run_id or os.getenv("BODHAN_RUN_ID", "default")
        self.ledger_path = ledger_path

    def estimate_cost(
        self,
        file_path: str | Path,
        max_pages: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Performs pre-flight cost estimation for a document without making any HTTP calls.

        Returns:
            Dict containing page count, processed pages, capped flag, and estimated cost in INR.
        """
        path_obj = Path(file_path).resolve()
        pages_cap = max_pages or self.default_max_pages

        if not path_obj.exists():
            return {
                "file_path": str(file_path),
                "error": f"File not found: {file_path}",
                "page_count": 0,
                "estimated_cost_inr": 0.0,
            }

        suffix = path_obj.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            return {
                "file_path": str(file_path),
                "error": f"Unsupported file type: {suffix}",
                "page_count": 0,
                "estimated_cost_inr": 0.0,
            }

        if suffix in SUPPORTED_IMAGE_EXTENSIONS:
            return {
                "file_path": str(file_path),
                "total_pages": 1,
                "pages_to_process": 1,
                "capped": False,
                "estimated_cost_inr": PRICE_OCR_PER_PAGE_INR,
            }

        if suffix == ".pdf":
            try:
                doc = pymupdf.open(str(path_obj))
                total_pages = doc.page_count
                doc.close()
                pages_to_process = min(total_pages, pages_cap)
                capped = total_pages > pages_cap
                cost = calculate_ocr_cost_inr(pages_to_process)
                return {
                    "file_path": str(file_path),
                    "total_pages": total_pages,
                    "pages_to_process": pages_to_process,
                    "capped": capped,
                    "estimated_cost_inr": cost,
                }
            except Exception as e:
                return {
                    "file_path": str(file_path),
                    "error": f"Failed to open PDF: {str(e)}",
                    "page_count": 0,
                    "estimated_cost_inr": 0.0,
                }

        return {
            "file_path": str(file_path),
            "error": "Unknown error",
            "page_count": 0,
            "estimated_cost_inr": 0.0,
        }

    def _extract_pages(
        self, file_path: Path, max_pages: int
    ) -> Tuple[List[Tuple[int, bytes, str]], int, bool, Optional[StructuredError]]:
        """Renders PDF or image pages into raw image bytes with page indices.

        Returns:
            (pages_list, total_doc_pages, is_capped, error)
        """
        suffix = file_path.suffix.lower()

        # Direct Image
        if suffix in SUPPORTED_IMAGE_EXTENSIONS:
            try:
                img_bytes = file_path.read_bytes()
                mime = "image/png" if suffix == ".png" else "image/jpeg"
                return [(1, img_bytes, mime)], 1, False, None
            except Exception as e:
                return (
                    [],
                    0,
                    False,
                    StructuredError(
                        code="FILE_READ_ERROR",
                        message=f"Failed to read image file '{file_path}': {str(e)}",
                        retryable=False,
                    ),
                )

        # PDF Document Rendering via PyMuPDF
        if suffix == ".pdf":
            try:
                doc = pymupdf.open(str(file_path))
                total_pages = doc.page_count
                if total_pages == 0:
                    doc.close()
                    return (
                        [],
                        0,
                        False,
                        StructuredError(
                            code="EMPTY_DOCUMENT",
                            message=f"PDF document '{file_path}' contains zero pages.",
                            retryable=False,
                        ),
                    )

                limit = min(total_pages, max_pages)
                capped = total_pages > max_pages

                pages_data: List[Tuple[int, bytes, str]] = []
                for i in range(limit):
                    page = doc.load_page(i)
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    pages_data.append((i + 1, img_bytes, "image/png"))

                doc.close()
                return pages_data, total_pages, capped, None
            except Exception as e:
                return (
                    [],
                    0,
                    False,
                    StructuredError(
                        code="PDF_RENDER_ERROR",
                        message=f"Failed to render PDF '{file_path}' using PyMuPDF: {str(e)}",
                        retryable=False,
                    ),
                )

        return (
            [],
            0,
            False,
            StructuredError(
                code="UNSUPPORTED_MEDIA_TYPE",
                message=f"Unsupported file format '{suffix}'. Supported formats: PNG, JPG, JPEG, WEBP, PDF.",
                retryable=False,
            ),
        )

    async def _ocr_single_page(
        self,
        client: httpx.AsyncClient,
        img_bytes: bytes,
        mime_type: str,
        call_tracker: CallTracker,
    ) -> Tuple[Optional[str], Optional[StructuredError]]:
        """Performs a single OCR request for one page image."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Encode image bytes to base64 internally
        b64_data = base64.b64encode(img_bytes).decode("ascii")
        data_uri = f"data:{mime_type};base64,{b64_data}"

        payload = {
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        }
                    ],
                }
            ],
        }

        # Live-call safety guard: block real HTTP when BODHAN_ALLOW_LIVE is 0/false
        guard_err = check_live_calls_allowed(url, client)
        if guard_err:
            return None, guard_err

        # Global spend cap guard
        est_cost = PRICE_OCR_PER_PAGE_INR
        cap_err = check_global_cap_allowed(est_cost, self.ledger_path)
        if cap_err:
            return None, cap_err

        max_retries = 2
        response_2xx: Optional[httpx.Response] = None
        last_err: Optional[StructuredError] = None
        call_id: Optional[str] = None
        fffd_retry_count = 0

        for attempt in range(max_retries + 1):
            if not call_tracker.record_call():
                return None, StructuredError(
                    code="CALL_LIMIT_EXCEEDED",
                    message=f"Exceeded maximum allowed HTTP calls ({call_tracker.max_calls}) for this invocation.",
                    retryable=False,
                )

            # Write ledger line with status "dispatched" BEFORE real HTTP send
            call_id = None
            if not is_mock_transport(client) or os.getenv("BODHAN_RECORD_MOCK_SPEND") == "1":
                call_id = log_spend_dispatched(
                    model=MODEL_NAME,
                    estimated_cost_inr=est_cost,
                    caller="indic_ocr",
                    run_id=self.run_id,
                    ledger_path=self.ledger_path,
                )

            try:
                async with self.semaphore:
                    response = await client.post(url, headers=headers, json=payload, timeout=90.0)

                # Check if 2xx response was received
                if 200 <= response.status_code < 300:
                    # Check for U+FFFD using response's own decoding (response.json())
                    try:
                        parsed_data = response.json()
                        choices = parsed_data.get("choices", [])
                        msg_content = (
                            choices[0].get("message", {}).get("content", "") if choices else ""
                        )
                        if "\ufffd" in msg_content:
                            if fffd_retry_count < 1 and attempt < max_retries:
                                fffd_retry_count += 1
                                await asyncio.sleep(0.5)
                                continue
                            return None, StructuredError(
                                code="UNICODE_REPLACEMENT_CHAR",
                                message="OCR output contains invalid Unicode replacement character (U+FFFD).",
                                retryable=False,
                            )
                        response_2xx = response
                        break
                    except Exception:
                        response_2xx = response
                        break

                should_retry, sleep_s, err = classify_http_response(response, attempt, max_retries)
                last_err = err
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        http_status=response.status_code,
                        error_code=err.code if err else f"HTTP_{response.status_code}",
                        ledger_path=self.ledger_path,
                    )
                if should_retry:
                    await asyncio.sleep(sleep_s)
                    continue
                return None, err

            except httpx.TimeoutException:
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        error_code="TIMEOUT",
                        ledger_path=self.ledger_path,
                    )
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                    continue
                return None, StructuredError(
                    code="TIMEOUT",
                    message="Request to Bodhan Indic-OCR timed out.",
                    retryable=True,
                )
            except Exception as e:
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        error_code="NETWORK_ERROR",
                        ledger_path=self.ledger_path,
                    )
                return None, StructuredError(
                    code="NETWORK_ERROR",
                    message=f"Network error during Indic-OCR call: {str(e)}",
                    retryable=False,
                )

        if response_2xx is None:
            return None, last_err or StructuredError(
                code="REQUEST_FAILED", message="OCR request failed."
            )

        # Once a 2xx response was received, NEVER retry!
        # If any parsing exception occurs here, return StructuredError with UNKNOWN_BILLED
        try:
            if call_id:
                update_spend_ledger(
                    call_id=call_id,
                    status="completed",
                    http_status=response_2xx.status_code,
                    billed_units=1,
                    actual_cost_inr=PRICE_OCR_PER_PAGE_INR,
                    ledger_path=self.ledger_path,
                )

            data = response_2xx.json()
            choices = data.get("choices", [])
            if not choices:
                return None, StructuredError(
                    code="EMPTY_RESPONSE",
                    message="Bodhan Indic-OCR returned no choices in response.",
                    retryable=False,
                )

            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                return None, StructuredError(
                    code="PAGE_TRUNCATED",
                    message="Indic-OCR output was truncated due to token limit (finish_reason='length').",
                    retryable=False,
                )

            text = choice.get("message", {}).get("content", "")
            if not text and finish_reason != "stop":
                return None, StructuredError(
                    code="PAGE_INCOMPLETE",
                    message=f"Indic-OCR output was incomplete (finish_reason='{finish_reason}').",
                    retryable=False,
                )

            return text, None

        except Exception as e:
            return None, StructuredError(
                code="UNKNOWN_BILLED",
                message=f"Request succeeded with HTTP {response_2xx.status_code} and was billed, but response processing failed: {str(e)}",
                retryable=False,
            )

    async def parse_document(
        self,
        file_path: str | Path,
        max_pages: Optional[int] = None,
    ) -> OcrOutput:
        """Parses a document (image or PDF) into reading-ordered text per page."""
        path_obj = Path(file_path).resolve()
        pages_cap = max_pages or self.default_max_pages

        # 1. Pre-flight file validation
        if not path_obj.exists():
            return OcrOutput(
                success=False,
                file_path=str(file_path),
                error=StructuredError(
                    code="FILE_NOT_FOUND",
                    message=f"File not found: {file_path}",
                    retryable=False,
                ),
            )

        if not path_obj.is_file():
            return OcrOutput(
                success=False,
                file_path=str(file_path),
                error=StructuredError(
                    code="NOT_A_FILE",
                    message=f"Path is a directory or special device, not a regular file: {file_path}",
                    retryable=False,
                ),
            )

        suffix = path_obj.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            return OcrOutput(
                success=False,
                file_path=str(file_path),
                error=StructuredError(
                    code="UNSUPPORTED_MEDIA_TYPE",
                    message=f"Unsupported file format '{suffix}'. Supported formats: PNG, JPG, JPEG, WEBP, PDF.",
                    retryable=False,
                ),
            )

        # 2. Extract pages to image bytes
        pages_data, total_pages, capped, render_err = self._extract_pages(path_obj, pages_cap)
        if render_err:
            return OcrOutput(
                success=False,
                file_path=str(file_path),
                total_pages_in_doc=total_pages,
                pages_processed=0,
                error=render_err,
            )

        # 3. Check API key before sending requests
        if not self.api_key:
            return OcrOutput(
                success=False,
                file_path=str(file_path),
                total_pages_in_doc=total_pages,
                pages_processed=0,
                error=StructuredError(
                    code="AUTH_ERROR",
                    message="BODHAN_OCR_KEY is missing from environment.",
                    retryable=False,
                ),
            )

        call_tracker = CallTracker(max_calls=len(pages_data) * 2 + 2)
        page_results: List[PageResult] = []
        cached_count = 0
        billed_count = 0

        async def run_ocr_pipeline(client: httpx.AsyncClient):
            nonlocal cached_count, billed_count
            for page_num, img_bytes, mime_type in pages_data:
                cache_key = get_cache_key(MODEL_NAME, img_bytes)
                cached = read_cache(cache_key, self.cache_dir)
                if cached is not None:
                    if (
                        not is_mock_transport(self.custom_client)
                        or os.getenv("BODHAN_RECORD_MOCK_SPEND") == "1"
                    ):
                        log_spend_cache_hit(
                            model=MODEL_NAME,
                            caller="indic_ocr",
                            run_id=self.run_id,
                            ledger_path=self.ledger_path,
                        )
                    raw_cached_text = cached.get("text", "")
                    page_results.append(
                        PageResult(
                            page_number=page_num,
                            text=raw_cached_text,
                            cache_hit=True,
                        )
                    )
                    cached_count += 1
                    continue

                text, err = await self._ocr_single_page(client, img_bytes, mime_type, call_tracker)
                if err:
                    page_results.append(
                        PageResult(
                            page_number=page_num,
                            text="",
                            cache_hit=False,
                            error=err,
                        )
                    )
                    # Do not halt document on single page failure: continue to remaining pages
                    continue

                page_results.append(
                    PageResult(
                        page_number=page_num,
                        text=text or "",
                        cache_hit=False,
                    )
                )
                billed_count += 1

                # Cache raw page output
                if "\ufffd" not in (text or ""):
                    write_cache(
                        cache_key,
                        {"text": text, "page_number": page_num},
                        self.cache_dir,
                    )
            return None

        if self.custom_client:
            await run_ocr_pipeline(self.custom_client)
        else:
            async with httpx.AsyncClient() as client:
                await run_ocr_pipeline(client)

        successful_pages = [p for p in page_results if p.error is None]
        first_error = next((p.error for p in page_results if p.error is not None), None)
        success = len(successful_pages) > 0

        full_doc_text = "\n\n---\n\n".join(
            f"## Page {p.page_number}\n\n{p.text}" for p in page_results if p.text
        )
        total_cost = calculate_ocr_cost_inr(billed_count)

        return OcrOutput(
            success=success,
            file_path=str(file_path),
            total_pages_in_doc=total_pages,
            pages_processed=len(page_results),
            capped=capped,
            pages=page_results,
            full_text=full_doc_text,
            cached_pages_count=cached_count,
            billed_pages_count=billed_count,
            estimated_cost_inr=total_cost,
            error=first_error if not success else None,
        )
