"""ocr_document: images and multi-page PDFs -> Markdown, one image per API request."""
from __future__ import annotations

import asyncio
import base64
import io
import json

from .. import core
from ..core import PRICES, ToolError, register_heif
from ..languages import mixed_script_words, script_counts

MAX_SIDE = 2000
PAGE_CAP = 60


def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    out: list[int] = []
    try:
        for part in spec.split(","):
            a, _, b = part.strip().partition("-")
            out += range(int(a), int(b or a) + 1)
    except ValueError:
        raise ToolError("INVALID_ARGUMENT", f"bad page range '{spec}'", "Use e.g. '1-3,7'.")
    bad = [p for p in out if not 1 <= p <= total]
    if bad:
        raise ToolError("INVALID_ARGUMENT", f"pages {bad} out of range 1-{total}")
    return sorted(set(out))


def _png(im, max_side: int) -> bytes:
    im = im.convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((int(im.width * s), int(im.height * s)))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def render(path, pages: str | None, dpi: int, max_side: int = MAX_SIDE) -> list[tuple[int, bytes]]:
    from PIL import Image, ImageOps
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pymupdf as fitz
        try:
            doc = fitz.open(path)
        except Exception as e:
            raise ToolError("UNSUPPORTED_FORMAT", f"cannot open PDF: {e}")
        if doc.needs_pass:
            raise ToolError("UNSUPPORTED_FORMAT", f"{path.name} is password-protected", "Provide an unprotected copy.")
        nums = parse_pages(pages, doc.page_count)
        if not pages and len(nums) > PAGE_CAP:
            raise ToolError("TOO_LARGE", f"PDF has {len(nums)} pages (cap {PAGE_CAP} without 'pages')",
                            f"Pass pages=, e.g. '1-10'. Full cost would be ₹{len(nums) * PRICES['ocr_per_image']:.2f}.")
        out = []
        for n in nums:
            pix = doc[n - 1].get_pixmap(dpi=dpi)
            out.append((n, _png(Image.open(io.BytesIO(pix.tobytes("png"))), max_side)))
        return out
    register_heif()
    try:
        with Image.open(path) as im:
            return [(1, _png(ImageOps.exif_transpose(im), max_side))]  # honour EXIF rotation from phones
    except Exception as e:
        raise ToolError("UNSUPPORTED_FORMAT", f"cannot open image {path.name}: {e}", "Use PNG/JPEG/PDF.")


async def _ocr_page(n: int, png: bytes, table_format: str, stats: dict) -> tuple[str, list]:
    key = core.key_of("ocr", table_format, png)
    c = core.cache_get(key)
    if c is not None:
        stats["cached"] += 1
        j = json.loads(c)
        return j["md"], j["blocks"]
    core.reserve(PRICES["ocr_per_image"])
    try:
        uri = "data:image/png;base64," + base64.b64encode(png).decode()  # lives only in this HTTP call
        max_tokens = 2048
        while True:
            body = {"model": "indic-ocr", "table_format": table_format, "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": uri}}]}]}
            try:
                r = await core.post("ocr", "/v1/chat/completions", json=body)
                break
            except ToolError as e:
                if e.details.get("status") == 502 and max_tokens < 8192:  # block too long: raise limit
                    max_tokens *= 2
                    continue
                raise
        j = r.json()
        core.record("ocr", PRICES["ocr_per_image"], f"page {n}")
    finally:
        core.release(PRICES["ocr_per_image"])
    stats["cost"] += PRICES["ocr_per_image"]
    md = j["choices"][0]["message"]["content"] or ""
    blocks = [{k: b.get(k) for k in ("order", "label", "conf")} for b in j.get("blocks", [])]
    core.cache_put(key, json.dumps({"md": md, "blocks": blocks}).encode())
    return md, blocks


def _ink(png: bytes) -> float:
    """Share of dark pixels: a blank page has about none, a rotated page of text has plenty."""
    from PIL import Image
    g = Image.open(io.BytesIO(png)).convert("L")
    g.thumbnail((200, 200))
    h = g.histogram()
    return sum(h[:160]) / max(1, sum(h))


async def _page_with_rotation(n: int, png: bytes, table_format: str, stats: dict) -> tuple[str, list, int]:
    """OCR a page; if it came back empty although it has ink, retry rotated 90/270/180 degrees (cost per try)."""
    md, blocks = await _ocr_page(n, png, table_format, stats)
    if md.strip() or _ink(png) < 0.01:
        return md, blocks, 0
    from PIL import Image
    for deg in (90, 270, 180):
        im = Image.open(io.BytesIO(png)).rotate(deg, expand=True)
        b = io.BytesIO()
        im.save(b, "PNG")
        md, blocks = await _ocr_page(n, b.getvalue(), table_format, stats)
        if md.strip():
            return md, blocks, deg
    return "", [], 0


async def ocr_document(path: str, pages: str | None = None, table_format: str = "markdown",
                       dpi: int = 200, out_name: str | None = None) -> dict:
    if table_format not in ("markdown", "html"):
        raise ToolError("INVALID_ARGUMENT", "table_format must be markdown or html")
    p = core.resolve_in(path)
    imgs = await asyncio.to_thread(render, p, pages, dpi)
    stats = {"cost": 0.0, "cached": 0}
    res = await asyncio.gather(*[_page_with_rotation(n, b, table_format, stats) for n, b in imgs], return_exceptions=True)
    parts, failed, warnings, confs, unpaid = [], [], [], [], []
    for (n, _), r in zip(imgs, res):
        if isinstance(r, ToolError):
            if r.code == "BUDGET_EXCEEDED":  # keep the pages already paid for, report the rest
                unpaid.append(n)
                failed.append({"page": n, "error": "budget exhausted"})
                continue
            if r.code in ("AUTH_FAILED", "MISSING_API_KEY", "INSUFFICIENT_CREDITS"):
                raise r
            failed.append({"page": n, "error": r.message})
            warnings.append(f"page {n} could not be read: {r.message}")
            continue
        if isinstance(r, Exception):
            raise r
        md, blocks, rot = r
        md = md.replace("[Blank]", "")  # the model's placeholder for an empty table cell
        if rot:
            warnings.append(f"page {n} was empty as scanned; read after rotating {rot} degrees")
        if not md.strip():
            warnings.append(f"page {n} has no text")
        confs += [b["conf"] for b in blocks if b.get("conf") is not None]
        parts.append(f"<!-- page {n} -->\n{md.strip()}")
    text = "\n\n".join(parts)
    if not parts and unpaid:
        raise ToolError("BUDGET_EXCEEDED", "budget exhausted before any page could be read",
                        "Stop and report; raise --max-cost-inr to process the document.")
    if not parts:
        raise ToolError("UPSTREAM_ERROR", "OCR failed on every page", "", failures=failed)
    if unpaid:
        warnings.append(f"BUDGET EXHAUSTED: read {len(parts)} of {len(imgs)} pages; pages {unpaid} not done. Raise the cost cap to finish.")
    sc = script_counts(text)
    mixed = mixed_script_words(text)
    if mixed:
        warnings.append(f"{len(mixed)} word(s) mix two Indic scripts (likely misread characters), e.g. {mixed[:3]}")
    out = core.write_text(out_name or f"ocr_{core.key_of(str(p), pages or '')[:10]}.md", text)
    return {"markdown_ref": str(out), "preview": core.preview(text), "pages_total": len(imgs),
            "pages_done": len(parts), "pages_failed": failed,
            "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
            "dominant_script": max(sc, key=sc.get) if sc else None, "chars": len(text),
            "cached_pages": stats["cached"], "cost_inr": round(stats["cost"], 4), "warnings": warnings}
