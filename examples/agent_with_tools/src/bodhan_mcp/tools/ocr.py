"""Indic-OCR client: read text from an image or a multi-page PDF. Billed per page.

The model reads one PNG/JPEG page per request. PDFs are rendered to one PNG per page with pypdfium2
(pure Python wheels, no system dependency) and read page by page, so callers never split documents.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
from pathlib import Path

import httpx
from pydantic import BaseModel

from bodhan_core.cache import ResultCache
from bodhan_core.client import post
from bodhan_core.config import section
from bodhan_core.errors import ToolError, check_input_file
from bodhan_core.runtime import BACKOFF_S, make_client

_CFG = section("ocr")
INR_PER_PAGE = _CFG["inr_per_page"]
MODEL = "indic-ocr"
MAX_IMAGE_BYTES = _CFG["max_image_bytes"]
PDF_DPI = _CFG["pdf_dpi"]
MAX_PAGES = _CFG["max_pages"]
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

_CACHE: ResultCache[str] = ResultCache()  # page image hash -> markdown, so the same page is never sent twice per run


class OcrPage(BaseModel):
    page: int
    markdown: str


class OcrResult(BaseModel):
    ok: bool
    markdown: str | None = None  # all pages joined, with a "<!-- page N -->" marker before each when there are several
    pages: list[OcrPage] = []
    page_count: int = 0  # pages in the document (may exceed len(pages) when a page range was requested)
    cost_inr: float = 0.0
    error: ToolError | None = None


def _fail(code: str, message: str, retryable: bool = False) -> OcrResult:
    return OcrResult(ok=False, error=ToolError(code=code, message=message, retryable=retryable))


def _render_pdf(data: bytes, first: int, last: int | None) -> tuple[list[tuple[int, bytes]], int] | ToolError:
    """Render pages [first, last] (1-based) to PNG. Returns ([(page number, png bytes)], total pages in the PDF)."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:  # pypdfium2 raises PdfiumError for corrupt / password-protected files
        return ToolError(code="bad_pdf", message=f"Could not open the PDF (corrupt or password-protected?): {exc}. Provide an unprotected, valid PDF.")
    total = len(pdf)
    end = min(last or total, total)
    if first < 1 or first > end:
        return ToolError(code="bad_page_range", message=f"PDF has {total} pages; first_page={first}, last_page={last} is not a valid range. Choose pages between 1 and {total}.")
    if end - first + 1 > MAX_PAGES:
        return ToolError(code="too_many_pages", message=f"{end - first + 1} pages requested; the limit per call is {MAX_PAGES}. Call again with first_page / last_page to read the document in parts.")
    pages = []
    for n in range(first, end + 1):
        buf = io.BytesIO()
        pdf[n - 1].render(scale=PDF_DPI / 72).to_pil().convert("RGB").save(buf, format="PNG")
        pages.append((n, buf.getvalue()))
    return pages, total


async def _ocr_page(client: httpx.AsyncClient, key: str, png_or_jpeg: bytes, mime: str, backoff_s: float) -> tuple[str, bool] | ToolError:
    """OCR one page image. Returns (markdown, billed)."""
    digest = hashlib.sha256(png_or_jpeg).hexdigest()
    if digest in _CACHE:
        return _CACHE[digest], False
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(png_or_jpeg).decode()}"}},
        ]}],
    }  # fmt: skip
    res = await post(client, "/v1/chat/completions", key, backoff_s, json=body)
    if isinstance(res, ToolError):
        return res
    try:
        markdown = res.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError):
        return ToolError(code="bad_response", message=f"Unexpected response from Bodhan: {res.text[:300]}")
    _CACHE[digest] = markdown
    return markdown, True


async def ocr_document(
    file_path: str,
    first_page: int = 1,
    last_page: int | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    backoff_s: float = BACKOFF_S,
) -> OcrResult:
    path = Path(file_path)
    if err := check_input_file(path, "file_path", "PNG, JPEG or PDF"):
        return OcrResult(ok=False, error=err)
    suffix = path.suffix.lower()
    if suffix != ".pdf" and suffix not in _MIME:
        return _fail("unsupported_format", f"`{path.name}` has extension `{suffix or '(none)'}`. Supported inputs are .png, .jpg, .jpeg and .pdf; convert the file and call again.")
    if last_page is not None and last_page < first_page:
        return _fail("bad_page_range", f"`last_page` ({last_page}) is before `first_page` ({first_page}). Use first_page <= last_page, or omit last_page.")
    data = path.read_bytes()
    if suffix != ".pdf" and len(data) > MAX_IMAGE_BYTES:
        return _fail("file_too_large", f"Image is {len(data):,} bytes; the limit is {MAX_IMAGE_BYTES:,}. Downscale or recompress the image, or crop it, and call again.")
    key = os.environ.get("BODHAN_OCR_KEY")
    if not key:
        return _fail("missing_api_key", "BODHAN_OCR_KEY is not set in the server environment.")

    if suffix == ".pdf":
        rendered = _render_pdf(data, first_page, last_page)
        if isinstance(rendered, ToolError):
            return OcrResult(ok=False, error=rendered)
        images, total = rendered
        mime = "image/png"
    else:
        images, total, mime = [(1, data)], 1, _MIME[suffix]

    pages: list[OcrPage] = []
    billed = 0
    async with make_client(transport=transport) as client:
        for number, image in images:
            res = await _ocr_page(client, key, image, mime, backoff_s)
            if isinstance(res, ToolError):
                return _fail(res.code, f"Page {number} failed ({len(pages)} pages read before it): {res.message}", res.retryable)
            pages.append(OcrPage(page=number, markdown=res[0]))
            billed += res[1]

    if len(pages) == 1 and suffix != ".pdf":
        joined = pages[0].markdown
    else:
        joined = "\n\n".join(f"<!-- page {p.page} -->\n{p.markdown}" for p in pages)
    return OcrResult(ok=True, markdown=joined, pages=pages, page_count=total, cost_inr=round(billed * INR_PER_PAGE, 6))
