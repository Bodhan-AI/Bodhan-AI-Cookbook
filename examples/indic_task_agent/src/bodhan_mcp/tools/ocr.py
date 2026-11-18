"""ocr_document: one page per request, headings rebuilt, tables kept."""
from __future__ import annotations

import json
from pathlib import Path

from ..core import api, cache, config, costs, media
from ..core import text as txt
from ..core.errors import BodhanError
from ..core.schemas import OcrDocument, PageResult

_HEADINGS = {"Chapter-title": 1, "Title": 1, "Section-title": 2, "Sub-section-title": 3}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


def ocr_document(
    input_path: str,
    pages: str | None = None,
    table_format: str = "html",
    dpi: int = 300,
    max_tokens: int = 2048,
    out_dir: str | None = None,
    max_cost_inr: float | None = None,
    force: bool = False,
) -> OcrDocument:
    """Extract text and tables from an image or a scanned multi-page PDF into a Markdown file. PDFs are rendered page by page and sent one at a time, so any page count works in one call; image inputs (PNG, JPEG, WebP, TIFF) are converted locally. Reading order is preserved, page breaks are marked with ---, and headings are rebuilt from the layout labels the model returns. Tables come as HTML by default, which keeps merged cells; use table_format="markdown" for plain Markdown. `pages` selects pages ("1-4", "2,5"); omit for all. Returns the Markdown path, a JSON path with per-page layout blocks, and per-page confidence."""
    src = Path(input_path)
    if not src.is_file():
        raise BodhanError("MISSING_FILE", f"{input_path} does not exist.", hint="Pass a path to an existing PDF or image.")
    if table_format not in ("html", "markdown"):
        raise BodhanError("BAD_REQUEST", f"table_format must be html or markdown, not '{table_format}'.")
    suffix = src.suffix.lower()
    out = Path(out_dir) if out_dir else config.default_out_dir()
    out.mkdir(parents=True, exist_ok=True)
    budget = costs.CallBudget(max_cost_inr)

    if suffix == ".pdf":
        total = media.pdf_page_count(str(src))
        wanted = txt.parse_pages(pages, total)
        if not wanted:
            raise BodhanError("BAD_REQUEST", f"pages='{pages}' selects nothing in a {total}-page document.", hint="Use 1-based page numbers or ranges.")
        page_iter = ((i, media.render_pdf_page(str(src), i, dpi)) for i in wanted)
    elif suffix in _IMAGE_SUFFIXES:
        wanted, total = [0], 1
        page_iter = iter([(0, media.image_to_jpeg(str(src)))])
    else:
        raise BodhanError("UNSUPPORTED_FORMAT", f"{src.name} is not a PDF or image.", hint="OCR accepts .pdf, .png, .jpg, .jpeg, .webp, .tif.")

    budget.reserve(costs.ocr_cost(len(wanted)), f"OCR of {len(wanted)} page(s)")
    results: list[PageResult] = []
    sections: list[str] = []
    all_blocks: dict[str, list[dict]] = {}
    warnings: list[str] = []
    for index, jpeg in page_iter:
        key = cache.digest("ocr", table_format, dpi, max_tokens, jpeg)
        cached = None if force else cache.get_json(key)
        if isinstance(cached, dict):
            result = cached
            hit = True
        else:
            result = _ocr_page(jpeg, table_format, max_tokens)
            if not force:
                cache.put_json(key, result)
            hit = False
        markdown = _to_markdown(result)
        sections.append(markdown)
        all_blocks[f"page_{index + 1}"] = result.get("blocks") or []
        confs = [b.get("conf") for b in (result.get("blocks") or []) if isinstance(b.get("conf"), (int, float))]
        mean_conf = round(sum(confs) / len(confs), 3) if confs else None
        page_warnings: list[str] = []
        if not markdown.strip():
            page_warnings.append("blank_page")
            warnings.append(f"blank_page_{index + 1}")
        if mean_conf is not None and mean_conf < 0.5:
            page_warnings.append("low_confidence")
            warnings.append(f"low_confidence_page_{index + 1}")
        if result.get("retried"):
            warnings.append(f"table_retried_at_4096_page_{index + 1}")
        results.append(
            PageResult(
                index=index,
                chars=len(markdown),
                mean_detection_conf=mean_conf,
                cost_inr=0.0 if hit else costs.OCR_PER_IMAGE,
                cache_hit=hit,
                retried_max_tokens=bool(result.get("retried")),
                warnings=page_warnings,
            )
        )
    if all(not s.strip() for s in sections):
        raise BodhanError("OCR_FAILED", "No text could be extracted from any page.", hint="Check the scan quality and that the page contains printed text.")

    md_path = out / f"{src.stem}.ocr.md"
    md_path.write_text("\n\n---\n\n".join(sections), encoding="utf-8")
    blocks_path = out / f"{src.stem}.ocr.blocks.json"
    blocks_path.write_text(json.dumps(all_blocks, ensure_ascii=False), encoding="utf-8")
    spent = costs.ocr_cost(sum(0 if r.cache_hit else 1 for r in results))
    budget.add(spent)
    return OcrDocument(
        markdown_path=str(md_path.resolve()),
        blocks_path=str(blocks_path.resolve()),
        page_count=len(results),
        pages=results,
        cost_inr=round(spent, 4),
        warnings=warnings,
    )


def _ocr_page(jpeg: bytes, table_format: str, max_tokens: int) -> dict:
    """One page. A block over its token budget (HTTP 502, or finish_reason=length) is retried at 4096 then 8192."""
    ladder = [max_tokens] + [t for t in (4096, 8192) if t > max_tokens]
    result: dict = {}
    for attempt, tokens in enumerate(ladder):
        try:
            result = api.ocr_page(jpeg, table_format, tokens)
            overflow = result.get("finish_reason") == "length"
        except BodhanError as exc:
            if not (exc.status and exc.status >= 500 and exc.code == "UPSTREAM_ERROR"):
                raise
            if attempt == len(ladder) - 1:
                raise BodhanError("OCR_FAILED", f"A block on this page exceeded {tokens} tokens even after retries.", hint="Split the page or use table_format='markdown'.", request_id=exc.request_id) from None
            overflow = True
        if not overflow:
            break
        if attempt == len(ladder) - 1:
            raise BodhanError("OCR_FAILED", f"The OCR output was still cut off at {tokens} tokens.", hint="Split the page or use table_format='markdown'.")
        result = {"retried": True}
    result["retried"] = attempt > 0
    if not (result.get("content") or "").strip() and not result.get("blocks"):
        raise BodhanError("OCR_FAILED", "The OCR model returned an empty page.", hint="The page may be blank or unreadable.")
    return result


def _to_markdown(result: dict) -> str:
    """Prefer block-level Markdown so headings get their level; fall back to the model's own content."""
    blocks = sorted(result.get("blocks") or [], key=lambda b: b.get("order", 0))
    if not blocks:
        return (result.get("content") or "").strip()
    out: list[str] = []
    for block in blocks:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        level = _HEADINGS.get(block.get("label", ""))
        out.append(f"{'#' * level} {text}" if level else text)
    rebuilt = "\n\n".join(out).strip()
    return rebuilt or (result.get("content") or "").strip()
