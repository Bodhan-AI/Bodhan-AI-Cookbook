"""Document tools: inspect_files and ocr_document."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import errors as E
from ..documents import (
    MAX_PDF_PAGES,
    classify,
    load_image,
    parse_page_spec,
    pdf_page_count,
    pdf_text_layer,
    read_text_file,
    render_pdf_page,
    text_layer_is_usable,
)
from ..languages import detect_language
from ..media import probe
from ..runtime import Artifact, RunContext, price_ocr
from ..textutil import preview
from .common import frame_untrusted, output_name

MAX_LISTED = 200


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InspectIn(_Strict):
    paths: list[str] = Field(description="Files or folders to inspect. Folders are listed recursively (up to 200 files).", min_length=1)


def _describe(p: Path) -> dict:
    kind = classify(p)
    d: dict = {"path": str(p), "name": p.name, "kind": kind, "bytes": p.stat().st_size}
    try:
        if kind in ("audio", "video"):
            info = probe(p)
            d.update({"duration_s": round(info.duration, 2) if info.duration else None, "has_audio": info.has_audio,
                      "has_video": info.has_video, "audio_codec": info.audio_codec})
            if info.duration:
                d["asr_chunks_estimate"] = int(info.duration // 28) + 1
        elif kind == "pdf":
            n = pdf_page_count(p)
            first = pdf_text_layer(p, 0) if n else ""
            d.update({"pages": n, "text_layer_on_page1": text_layer_is_usable(first),
                      "suggested_tool": "ocr_document (uses a clean text layer when present, OCR otherwise)"})
        elif kind == "image":
            from PIL import Image

            with Image.open(p) as im:
                d.update({"width": im.size[0], "height": im.size[1], "suggested_tool": "ocr_document"})
        elif kind == "text":
            text = read_text_file(p)
            d.update({"chars": len(text), "language": detect_language(text).as_dict(), "preview": preview(text, 200),
                      "content_kind": "untrusted_data"})
    except E.ToolError as exc:
        d["error"] = exc.as_dict()
    except Exception as exc:  # unreadable file: report, keep listing others
        d["error"] = {"code": E.MEDIA_ERROR, "message": f"could not inspect: {exc}"}
    return d


async def inspect_files(ctx: RunContext, a: InspectIn) -> dict:
    found: list[dict] = []
    missing: list[str] = []
    for raw in a.paths:
        try:
            p = ctx.resolve_input(raw)
        except E.ToolError as exc:
            if exc.code in (E.FILE_NOT_FOUND, E.PATH_NOT_ALLOWED):
                missing.append(raw)
                continue
            raise
        files = sorted(x for x in p.rglob("*") if x.is_file() and not x.name.startswith(".")) if p.is_dir() else [p]
        for f in files[:MAX_LISTED]:
            found.append(await asyncio.to_thread(_describe, f))
    if not found and missing:
        raise E.ToolError(E.FILE_NOT_FOUND, f"None of the paths exist: {missing}", hint="Report that the input files are missing.")
    return {"files": found, "missing": missing, "note": "Metadata only; no model was called and nothing was billed."}


class OcrIn(_Strict):
    path: str = Field(description="Image (PNG/JPEG/WebP/TIFF/...) or PDF (scanned or digital, multi-page).")
    pages: str = Field("all", description="Pages for PDFs, e.g. 'all', '1-3,5'. Ignored for images.")
    table_format: Literal["markdown", "html"] = Field("markdown", description="How tables are returned. 'markdown' is easiest to translate; 'html' keeps merged cells.")
    use_text_layer: bool = Field(True, description="For PDFs, use the embedded text layer when it looks clean instead of paying for OCR.")
    output_name: str | None = Field(None, description="File name for the Markdown artifact.")


async def _ocr_image(ctx: RunContext, data: bytes, mime: str, table_format: str) -> dict:
    try:
        meta, _ = await ctx.client.ocr(data, mime, table_format=table_format)
    except E.ToolError as exc:
        if exc.code not in (E.UPSTREAM_REJECTED, E.UPSTREAM_UNAVAILABLE, E.TRUNCATED_OUTPUT) or "max_tokens" not in exc.message.lower():
            raise
        ctx.emit({"type": "recovery", "action": "ocr_retry_larger_max_tokens"})
        meta, _ = await ctx.client.ocr(data, mime, table_format=table_format, max_tokens=4096)
    return meta


async def ocr_document(ctx: RunContext, a: OcrIn) -> dict:
    p = ctx.resolve_input(a.path)
    kind = classify(p)
    if kind not in ("pdf", "image"):
        raise E.ToolError(E.UNSUPPORTED_FILE_TYPE, f"{p.name} is a {kind} file; ocr_document reads images and PDFs.",
                          hint={"text": "Use read_text.", "audio": "Use transcribe_audio.", "video": "Use transcribe_audio."}.get(kind, ""))
    pages_out: list[dict] = []
    if kind == "image":
        img = await asyncio.to_thread(load_image, p)
        page_list = [1]
        if not ctx.ledger.can_afford(price_ocr(1)):
            ctx.ledger.reserve("indic-ocr", price_ocr(1), "ocr")  # raises a structured budget error
        meta = await _ocr_image(ctx, img.data, img.mime, a.table_format)
        pages_out.append({"page": 1, "source": "indic-ocr", "markdown": meta["markdown"], "blocks": meta["blocks"]})
    else:
        count = pdf_page_count(p)
        page_list = parse_page_spec(a.pages, count)
        if len(page_list) > MAX_PDF_PAGES:
            raise E.ToolError(E.INVALID_ARGUMENT, f"{len(page_list)} pages requested; the limit is {MAX_PDF_PAGES} per call.",
                              hint="Pass a page range, e.g. pages='1-30', and call again for the rest.")
        layers = {n: (await asyncio.to_thread(pdf_text_layer, p, n - 1) if a.use_text_layer else "") for n in page_list}
        need_ocr = [n for n in page_list if not text_layer_is_usable(layers[n])]
        if not ctx.ledger.can_afford(price_ocr(len(need_ocr))):
            raise E.ToolError(E.BUDGET_EXCEEDED, f"OCR of {len(need_ocr)} page(s) needs ₹{price_ocr(len(need_ocr)):.2f}; "
                              f"only ₹{max(ctx.ledger.remaining or 0, 0):.2f} remains.",
                              hint="OCR fewer pages (pages='1-3') or stop and report.",
                              details={"needed_inr": price_ocr(len(need_ocr)), "remaining_inr": round(max(ctx.ledger.remaining or 0, 0), 4)})

        async def page(n: int) -> dict:
            if n not in need_ocr:
                return {"page": n, "source": "pdf-text-layer", "markdown": layers[n].strip(), "blocks": []}
            img = await asyncio.to_thread(render_pdf_page, p, n - 1)
            meta = await _ocr_image(ctx, img.data, img.mime, a.table_format)
            return {"page": n, "source": "indic-ocr", "markdown": meta["markdown"], "blocks": meta["blocks"]}

        pages_out = list(await asyncio.gather(*[page(n) for n in page_list]))

    notes = []
    empty = [pg["page"] for pg in pages_out if not pg["markdown"].strip()]
    if empty:
        notes.append(f"no text found on page(s) {empty}")
    low = sum(1 for pg in pages_out for b in pg["blocks"] if (b.get("conf") or 1) < 0.4 and b.get("text"))
    if low:
        notes.append(f"{low} text block(s) had low layout-detection confidence")
    if any(pg["source"] == "pdf-text-layer" for pg in pages_out):
        notes.append("some pages used the PDF's embedded text layer (no OCR needed)")
    multi = len(pages_out) > 1
    md = "\n\n".join((f"<!-- page {pg['page']} -->\n" if multi else "") + pg["markdown"].strip() for pg in pages_out)
    dest = ctx.artifact_path(output_name(a.output_name, str(p), "_ocr"), ".md")
    dest.write_text(md, encoding="utf-8")
    det = detect_language(md)
    ctx.register(Artifact(dest, "ocr", "ocr_document", det.language, "indic-ocr", None, notes))
    if ctx.provenance_guard is not None:
        ctx.provenance_guard.add(md)
    tables = sum(1 for pg in pages_out for b in pg["blocks"] if b.get("type") == "Table")
    return {"path": str(dest), "pages": page_list, "pages_ocr": sum(1 for pg in pages_out if pg["source"] == "indic-ocr"),
            "chars": len(md), "tables_detected": tables, "detection": det.as_dict(), "notes": notes,
            "content": frame_untrusted(md, str(dest), 1500)}
