"""Indic-OCR client: read text from an image or a multi-page PDF. Billed per page.

The model reads one PNG/JPEG page per request. PDFs are rendered to one PNG per page with pypdfium2
(pure Python wheels, no system dependency) and read page by page, so callers never split documents.
"""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path

import httpx
from pydantic import BaseModel

from bodhan_core.cache import ResultCache
from bodhan_core.client import post
from bodhan_core.config import api_key, section
from bodhan_core.errors import ToolError, check_input_file
from bodhan_core.langid import describe
from bodhan_core.runtime import BACKOFF_S, make_client
from bodhan_core.textio import inline, save_to

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
    chars: int  # length of that page's Markdown (the text itself is in `markdown` / `markdown_path`)


class OcrResult(BaseModel):
    ok: bool
    markdown: str | None = None  # all pages joined, with a "<!-- page N -->" marker before each when there are several; only a preview (empty for Indian-language text) when `markdown_is_preview`
    markdown_is_preview: bool = False  # true: read the whole text from `markdown_path`
    markdown_path: str | None = None  # file holding the full Markdown (set for long documents or when output_path is given)
    pages: list[OcrPage] = []
    page_count: int = 0  # pages in the document (may exceed len(pages) when a page range was requested)
    cost_inr: float = 0.0
    detected_language: str | None = None  # from the extracted text's script; null when it has no letters
    script: str | None = None
    language_candidates: list[str] = []  # languages sharing that script (Devanagari: hi, mr, ne, ...)
    error: ToolError | None = None


def _fail(code: str, message: str, retryable: bool = False, cost_inr: float = 0.0) -> OcrResult:
    return OcrResult(ok=False, cost_inr=cost_inr, error=ToolError(code=code, message=message, retryable=retryable))  # cost_inr: pages already billed


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
    output_path: str | None = None,
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
    key = api_key("BODHAN_OCR_KEY")
    if not key:
        return _fail("missing_api_key", "BODHAN_OCR_KEY (or BODHAN_API_KEY) is not set in the server environment.")

    if suffix == ".pdf":
        rendered = _render_pdf(data, first_page, last_page)
        if isinstance(rendered, ToolError):
            return OcrResult(ok=False, error=rendered)
        images, total = rendered
        mime = "image/png"
    else:
        images, total, mime = [(1, data)], 1, _MIME[suffix]

    pages: list[OcrPage] = []
    texts: list[tuple[int, str]] = []
    billed = 0
    async with make_client(transport=transport) as client:
        for number, image in images:
            res = await _ocr_page(client, key, image, mime, backoff_s)
            if isinstance(res, ToolError):
                return _fail(res.code, f"Page {number} failed ({len(pages)} pages read before it): {res.message}", res.retryable, round(billed * INR_PER_PAGE, 6))
            texts.append((number, res[0]))
            pages.append(OcrPage(page=number, chars=len(res[0])))
            billed += res[1]

    if len(texts) == 1 and suffix != ".pdf":
        joined = texts[0][1]
    else:
        joined = "\n\n".join(f"<!-- page {n} -->\n{t}" for n, t in texts)
    cost = round(billed * INR_PER_PAGE, 6)
    saved, werr = save_to(joined, output_path, "ocr", ".md")
    if werr:
        return OcrResult(ok=False, error=werr, cost_inr=cost)
    shown, is_preview = inline(joined)
    return OcrResult(ok=True, markdown=shown, markdown_is_preview=is_preview, markdown_path=saved, pages=pages, page_count=total, cost_inr=cost, **describe(joined))
