"""indic-ocr: POST /v1/chat/completions. PNG/JPEG only, one base64 image per request."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field

from app.services.bodhan.capabilities import OCR_DEFAULT_MAX_TOKENS, OCR_TABLE_FORMATS
from app.services.bodhan.exceptions import (
    BodhanError, BodhanNotConfigured, BodhanProtocolError, BodhanRequestInvalid,
)
from app.services.documents.pdf import is_pdf, render_pdf_pages

SERVICE = "ocr"
MODEL = "indic-ocr"
# 502 can be retried ONLY by raising max_tokens (large table) - not by blind repetition.
_RETRY_STATUSES_EXCL_502 = frozenset({429, 500, 503, 504})
_DECODE_HINTS = ("decode", "decoded", "invalid image", "corrupt", "cannot identify", "not a valid image")


def sniff_image(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def build_payload(image_bytes: bytes, mime: str, *, table_format: str | None = None,
                  max_tokens: int | None = None) -> dict:
    payload: dict = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"}}]}],
    }
    if table_format is not None:
        payload["table_format"] = table_format
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


@dataclass
class OcrPageResult:
    page_number: int
    markdown: str = ""
    blocks: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_id: str | None = None
    max_tokens_used: int | None = None
    attempts: int = 1
    error: str | None = None
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class DocumentOcrResult:
    pages: list[OcrPageResult]

    @property
    def complete(self) -> bool:
        return bool(self.pages) and all(p.ok for p in self.pages)

    @property
    def failed_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if not p.ok]

    @property
    def combined_markdown(self) -> str:
        ordered = sorted((p for p in self.pages if p.ok), key=lambda p: p.page_number)
        return "\n\n".join(p.markdown for p in ordered if p.markdown)


def _token_ladder(requested: int | None, cap: int) -> list[int | None]:
    """First attempt uses the caller's value (or upstream default); retries raise it."""
    first = requested
    base = requested or OCR_DEFAULT_MAX_TOKENS
    ladder: list[int | None] = [first]
    nxt = base * 2
    while nxt <= cap:
        ladder.append(nxt)
        nxt *= 2
    if len(ladder) == 1 and base < cap:
        ladder.append(cap)
    return ladder


async def ocr_image(client, image_bytes: bytes, *, page_number: int = 1, table_format: str | None = None,
                    max_tokens: int | None = None, max_tokens_cap: int = 8192) -> OcrPageResult:
    mime = sniff_image(image_bytes)
    if mime is None:
        raise BodhanRequestInvalid("OCR accepts PNG or JPEG images only.", code="unsupported_image_format")
    if table_format is not None and table_format not in OCR_TABLE_FORMATS:
        raise BodhanRequestInvalid("table_format must be 'html' or 'markdown'.", code="invalid_table_format")
    last_error: BodhanError | None = None
    ladder = _token_ladder(max_tokens, max_tokens_cap)
    for attempt, tokens in enumerate(ladder, start=1):
        try:
            resp = await client.request(
                SERVICE, "POST", "/v1/chat/completions",
                json_body=build_payload(image_bytes, mime, table_format=table_format, max_tokens=tokens),
                retry_statuses=_RETRY_STATUSES_EXCL_502,
            )
        except BodhanError as exc:
            last_error = exc
            if exc.status_code == 502:
                text = (exc.detail or exc.message or "").lower()
                if any(h in text for h in _DECODE_HINTS):
                    raise  # the image itself is bad; more tokens cannot help
                continue  # most likely a block over max_tokens: retry higher
            raise
        body = resp.json
        try:
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
        except (KeyError, IndexError, TypeError):
            raise BodhanProtocolError(SERVICE, "Response did not contain choices[0].message.content.",
                                      status_code=resp.status_code, request_id=resp.request_id)
        blocks = body.get("blocks")  # top-level, NOT inside choices
        usage = body.get("usage") or {}
        return OcrPageResult(
            page_number, content, blocks if isinstance(blocks, list) else [],
            int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0),
            resp.request_id or body.get("id"), tokens, attempt)
    assert last_error is not None
    raise last_error


async def ocr_document(client, data: bytes, *, table_format: str | None = None,
                       max_tokens: int | None = None, max_tokens_cap: int = 8192, pdf_dpi: int = 200,
                       max_pdf_pages: int = 50) -> DocumentOcrResult:
    """OCR a PNG/JPEG (one page) or a PDF (rendered to PNG, one request per page, in order)."""
    if is_pdf(data):
        pages = await asyncio.to_thread(render_pdf_pages, data, dpi=pdf_dpi, max_pages=max_pdf_pages)
        images = [(p.page_number, p.png_bytes) for p in pages]
    elif sniff_image(data):
        images = [(1, data)]
    else:
        raise BodhanRequestInvalid("Unsupported document format. Use PNG, JPEG or PDF.",
                                   code="unsupported_image_format")
    results: list[OcrPageResult] = []
    for number, image in images:
        try:
            results.append(await ocr_image(client, image, page_number=number, table_format=table_format,
                                           max_tokens=max_tokens, max_tokens_cap=max_tokens_cap))
        except BodhanNotConfigured:
            raise
        except BodhanError as exc:
            if exc.status_code == 401:
                raise  # credentials are wrong; every page would fail the same way
            results.append(OcrPageResult(number, error=exc.message, error_code=exc.code or str(exc.status_code)))
    return DocumentOcrResult(sorted(results, key=lambda r: r.page_number))
