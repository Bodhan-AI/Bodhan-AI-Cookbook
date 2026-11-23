"""ocr_document: images and multi-page PDFs -> Markdown via Indic-OCR, one page per call."""

from __future__ import annotations

import asyncio
import io
import json
import re

from PIL import Image, ImageOps

from bodhan_core import langs, workspace
from bodhan_core.errors import ErrorCode, ToolError
from bodhan_mcp.runtime import get_client

RENDER_DPI = 200
MAX_LONG_SIDE = 2400  # downscale huge phone photos; OCR quality is fine and uploads stay small
DEFAULT_MAX_PAGES = 50
INLINE_TEXT_LIMIT = 1500  # short outputs come back inline, so callers need no read_text round trip


def parse_pages(spec: str | None, total: int) -> list[int]:
    """'1-3,5' -> [1, 2, 3, 5] (1-based). None -> all pages."""
    if not spec:
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"Bad page spec {part!r}.", hint='Use forms like "1-3,5".')
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b > total or a > b:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"Pages {part} out of range; the document has {total} page(s).",
                            hint=f'Use page numbers between 1 and {total}.')
        pages.extend(range(a, b + 1))
    return sorted(set(pages))


def _png(img: Image.Image) -> bytes:
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > MAX_LONG_SIDE:
        img.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _load_pages(path, kind: str, pages: str | None, max_pages: int) -> tuple[list[int], list[bytes], int]:
    if kind == "image":
        try:
            with Image.open(path) as img:
                return [1], [_png(img)], 1
        except Exception as e:  # noqa: BLE001
            raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"Cannot read image {path.name}: {e}",
                            hint="Use PNG, JPEG, WEBP, BMP or TIFF.") from None
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as e:  # noqa: BLE001
        raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"Cannot open PDF {path.name}: {e}",
                        hint="The PDF may be encrypted or corrupt.") from None
    with doc:
        total = len(doc)
        selected = parse_pages(pages, total)
        if len(selected) > max_pages:
            raise ToolError(ErrorCode.INVALID_ARGUMENT,
                            f"{len(selected)} pages selected but max_pages is {max_pages} (≈₹{0.2 * len(selected):.2f}).",
                            hint="Pass a narrower `pages` range, or raise max_pages deliberately.")
        images = [_png(doc[n - 1].render(scale=RENDER_DPI / 72).to_pil()) for n in selected]
    return selected, images, total


async def ocr_document(
    path: str,
    pages: str | None = None,
    table_format: str = "markdown",
    max_pages: int = DEFAULT_MAX_PAGES,
    out_dir: str | None = None,
) -> dict:
    p = workspace.resolve_input(path)
    kind = workspace.media_kind(p)
    if kind not in {"image", "pdf"}:
        raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"ocr_document reads images and PDFs, not {kind} files.",
                        hint="Use inspect_media to see which tool fits this file.")
    if table_format not in {"markdown", "html"}:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "table_format must be 'markdown' or 'html'.")
    if max_pages < 1:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "max_pages must be at least 1.")

    numbers, images, total = await asyncio.to_thread(_load_pages, p, kind, pages, max_pages)
    client = get_client()
    results = await asyncio.gather(
        *(client.ocr(img, mime="image/png", table_format=table_format) for img in images), return_exceptions=True
    )

    parts, page_texts, page_info, failed, cost, cached = [], [], [], [], 0.0, 0
    for n, r in zip(numbers, results, strict=True):
        if isinstance(r, ToolError):
            failed.append({"page": n, "error": r.info.model_dump(mode="json")})
            parts.append(f"<!-- page {n}: OCR failed ({r.info.code.value}) -->")
            continue
        if isinstance(r, BaseException):
            raise r
        text = ((r.data or {}).get("choices") or [{}])[0].get("message", {}).get("content") or ""
        cost += r.cost_inr
        cached += r.cached
        parts.append(f"<!-- page {n} -->\n{text.strip()}")
        page_texts.append(text)
        page_info.append({"page": n, "chars": len(text), "script": langs.detect_script(text),
                          "blocks": (r.data or {}).get("blocks", []), "cached": r.cached})

    if not page_info:
        first = failed[0]["error"]
        raise ToolError(ErrorCode(first["code"]), f"OCR failed on every page: {first['message']}", hint=first.get("hint"))

    markdown = "\n\n".join(parts) + "\n"
    out = workspace.output_dir(out_dir)
    key = f"{pages}|{table_format}"
    text_path = workspace.artifact_path(out, p, ".ocr.md", key=key)
    pages_path = workspace.artifact_path(out, p, ".ocr.pages.json", key=key)
    text_path.write_text(markdown, encoding="utf-8")
    pages_path.write_text(json.dumps(page_info, ensure_ascii=False, indent=1), encoding="utf-8")

    overall = langs.detect_script("\n".join(page_texts))  # page markers excluded: they are Latin
    return {
        "ok": True,
        "text_path": str(text_path),
        "pages_path": str(pages_path),
        "document_pages": total,
        "pages_processed": [i["page"] for i in page_info],
        "failed_pages": failed,
        "complete": not failed,
        "chars": len(markdown),
        "script": overall,
        "page_scripts": [{"page": i["page"], "script": i["script"]["script"], "candidates": i["script"]["candidates"]}
                         for i in page_info],
        "preview": workspace.preview(markdown),
        **({"text": markdown} if len(markdown) <= INLINE_TEXT_LIMIT else {}),
        "untrusted_content": True,
        "cost_inr": round(cost, 4),
        "cached_pages": cached,
    }
