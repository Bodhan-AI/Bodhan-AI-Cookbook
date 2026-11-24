"""ocr_document: text and tables from images and (scanned or digital) PDFs via Indic-OCR.

Indic-OCR takes one PNG/JPEG page per request, so PDFs are rendered page by page (pypdfium2),
images are normalised (EXIF rotation, size cap, PNG/JPEG), blank pages are skipped for free,
and pages run concurrently. A 502 (a layout block overflowed max_tokens) is retried once with a
larger max_tokens; a page that still fails is reported and the rest are returned.
"""

from __future__ import annotations

import asyncio
import re
import threading

from ..errors import BodhanToolError
from ..media import classify, load_image, pdf_page_count, render_pdf_page, resolve_input
from ..models import OcrPage, OcrResult
from ..text import detect_script
from ..usage import ocr_cost
from .common import get_context, new_ledger, output_dir, output_path, preview, report_spend_on_error, write_text

MAX_PAGES = 300
_PDFIUM_LOCK = threading.Lock()  # pdfium is not thread-safe


def parse_pages(spec: str | None, total: int) -> list[int]:
    """'1-3,5' (1-based) -> [0, 1, 2, 4]."""
    if not spec or not str(spec).strip() or str(spec).strip().lower() == "all":
        return list(range(total))
    out: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", part)
        if not m:
            raise BodhanToolError("invalid_input", f"Bad page range {part!r}; use forms like '1-3,5'.")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b > total or a > b:
            raise BodhanToolError("invalid_input", f"Page range {part!r} is outside 1-{total}.")
        out.extend(range(a - 1, b))
    return sorted(set(out))


def _render(path, index: int) -> tuple[bytes, str, bool]:
    with _PDFIUM_LOCK:
        return render_pdf_page(path, index)


@report_spend_on_error
async def ocr_document(
    path: str,
    pages: str | None = None,
    table_format: str = "markdown",
    output_dir_: str | None = None,
    max_cost_inr: float | None = None,
) -> OcrResult:
    src = resolve_input(path)
    if table_format not in {"markdown", "html"}:
        raise BodhanToolError("invalid_input", "table_format must be 'markdown' or 'html'.")
    kind = classify(src)
    if kind == "pdf":
        total = await asyncio.to_thread(pdf_page_count, src)
    elif kind == "image":
        total = 1
    elif kind in {"text", "document"}:
        raise BodhanToolError(
            "unsupported_file_type",
            f"{src.name} is already text; OCR is not needed.",
            hint="Use read_text to read it, or pass it straight to translate_text / synthesize_speech.",
        )
    else:
        raise BodhanToolError(
            "unsupported_file_type",
            f"{src.name} ({kind}) is not an image or PDF.",
            hint="For audio or video use transcribe_audio.",
        )
    if total == 0:
        raise BodhanToolError("invalid_input", f"{src.name} has no pages.")
    indexes = parse_pages(pages, total)
    if len(indexes) > MAX_PAGES:
        raise BodhanToolError(
            "invalid_input",
            f"{len(indexes)} pages requested; the limit per call is {MAX_PAGES}.",
            hint="Pass a page range.",
        )

    ledger = new_ledger(max_cost_inr)
    ledger.reserve(ocr_cost(len(indexes)), f"OCR of {len(indexes)} page(s)")
    client = get_context().client

    async def one(idx: int) -> tuple[str, str]:
        if kind == "pdf":
            img, mime, blank = await asyncio.to_thread(_render, src, idx)
        else:
            img, mime, blank = await asyncio.to_thread(load_image, src)
        if blank:
            return "", "blank_skipped"
        try:
            res = await client.ocr(img, mime=mime, table_format=table_format, max_tokens=None, ledger=ledger)
        except BodhanToolError as exc:
            if exc.details.get("status") != 502:
                raise
            res = await client.ocr(img, mime=mime, table_format=table_format, max_tokens=4096, ledger=ledger)
        return str(res.get("content") or "").strip(), "ok"

    results = await asyncio.gather(*(one(i) for i in indexes), return_exceptions=True)
    page_infos: list[OcrPage] = []
    parts: list[str] = []
    errors: list[BodhanToolError] = []
    for idx, res in zip(indexes, results, strict=True):
        if isinstance(res, BodhanToolError):
            errors.append(res)
            page_infos.append(OcrPage(page=idx + 1, chars=0, status="failed", error=res.code))
            parts.append(f"<!-- page {idx + 1}: OCR failed ({res.code}) -->")
            continue
        if isinstance(res, BaseException):
            raise res
        content, status = res
        page_infos.append(
            OcrPage(page=idx + 1, chars=len(content), detected_script=detect_script(content).dominant, status=status)
        )
        parts.append(f"<!-- page {idx + 1} -->\n{content}" if content else f"<!-- page {idx + 1}: blank -->")
    if errors and len(errors) == len(indexes):
        raise errors[0].with_details(pages_failed=len(errors))

    text = "\n\n".join(parts).strip() + "\n"
    out_dir = output_dir(output_dir_)
    fp = {"src": str(src), "size": src.stat().st_size, "pages": indexes, "tf": table_format}
    dest = write_text(output_path(out_dir, src.stem, "ocr", "md", fp), text)
    body = re.sub(r"<!--.*?-->", "", text)
    rep = detect_script(body)
    warnings = [f"Page {p.page} failed: {p.error}" for p in page_infos if p.status == "failed"]
    if not body.strip() and not errors:
        warnings.append("No text was found on the processed pages.")
    if rep.unsupported_script:
        warnings.append(f"Text is in {rep.dominant} script, which no Bodhan model supports.")
    return OcrResult(
        output_path=str(dest),
        preview=preview(re.sub(r"<!--.*?-->", " ", text)),
        pages_total=total,
        pages_processed=len(indexes),
        pages=page_infos,
        detected_script=rep.dominant,
        likely_languages=rep.likely_languages,
        has_tables=bool(re.search(r"^\s*\|.*\|\s*$", text, re.M)) or "<table" in text,
        complete=not errors,
        warnings=warnings,
        usage=ledger.usage(),
    )
