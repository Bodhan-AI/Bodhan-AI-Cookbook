"""Page images for Indic-OCR: PDF rasterisation and image normalisation.

PDFs are rendered with pypdfium2 (a pip wheel, so no poppler system dependency).
The PDF's embedded text layer is never used: every word comes from Indic-OCR.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import errors

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".heic"}
PDF_EXTS = {".pdf"}
MAX_LONG_SIDE = 2200  # px; beyond this OCR gains little and uploads get slow
PDF_DPI = 170
MAX_PAGES = 60


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # pypdfium2 raises PdfiumError, but be defensive about corrupt files
        raise errors.BodhanError(errors.UNSUPPORTED_MEDIA, f"Could not open {path.name} as a PDF: {exc}",
                                 hint="Check the file is a valid, unencrypted PDF.") from exc
    try:
        return len(pdf)
    finally:
        pdf.close()


def parse_page_spec(spec: str | None, count: int) -> list[int]:
    """'1-3,5' -> [1, 2, 3, 5] (1-based), clipped to the document."""
    if not spec:
        return list(range(1, count + 1))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            pages.extend(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            pages.append(int(part))
        else:
            raise errors.BodhanError(errors.INVALID_INPUT, f"Bad page spec {spec!r}.", hint="Use e.g. '1-3,5'.")
    valid = [p for p in dict.fromkeys(pages) if 1 <= p <= count]
    if not valid:
        raise errors.BodhanError(errors.INVALID_INPUT, f"Page spec {spec!r} selects no pages (document has {count}).")
    return valid


def _encode(img: Image.Image) -> tuple[bytes, str]:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    scale = MAX_LONG_SIDE / max(w, h)
    if scale < 1:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    if len(data) > 4_000_000:  # photos compress far better as JPEG
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=88)
        return buf.getvalue(), "image/jpeg"
    return data, "image/png"


def image_info(path: Path) -> dict:
    try:
        with Image.open(path) as img:
            return {"width": img.width, "height": img.height, "format": img.format}
    except (UnidentifiedImageError, OSError) as exc:
        raise errors.BodhanError(errors.UNSUPPORTED_MEDIA, f"Could not read {path.name} as an image: {exc}") from exc


def load_image_page(path: Path) -> tuple[bytes, str]:
    try:
        with Image.open(path) as img:
            img.load()
            return _encode(img)
    except (UnidentifiedImageError, OSError) as exc:
        raise errors.BodhanError(
            errors.UNSUPPORTED_MEDIA, f"Could not read {path.name} as an image: {exc}",
            hint="Use PNG, JPEG, WEBP or TIFF.",
        ) from exc


def render_pdf_pages(path: Path, pages: list[int], dpi: int = PDF_DPI) -> list[tuple[int, bytes, str]]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for p in pages:
            bitmap = pdf[p - 1].render(scale=dpi / 72)
            img = bitmap.to_pil()
            data, mime = _encode(img)
            out.append((p, data, mime))
        return out
    finally:
        pdf.close()
