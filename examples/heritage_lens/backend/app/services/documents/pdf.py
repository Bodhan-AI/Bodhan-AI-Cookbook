"""PDF -> PNG page rendering with PyMuPDF (imported lazily; Bodhan OCR does not accept PDFs)."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import AppError, ValidationFailed


class PdfDependencyMissing(AppError):
    status_code = 503
    code = "pdf_support_unavailable"
    message = "PDF support requires the PyMuPDF package (pip install PyMuPDF)."


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


@dataclass(frozen=True)
class RenderedPage:
    page_number: int  # 1-based
    png_bytes: bytes
    width: int
    height: int


def _import_mupdf():
    try:
        import pymupdf as mupdf  # PyMuPDF >= 1.24.3
        return mupdf
    except ImportError:
        try:
            import fitz as mupdf  # older name
            return mupdf
        except ImportError as exc:
            raise PdfDependencyMissing() from exc


def render_pdf_pages(data: bytes, *, dpi: int = 200, max_pages: int = 50,
                     max_side_px: int = 4000) -> list[RenderedPage]:
    """Render every page, in order. Refuses (rather than truncates) PDFs over max_pages."""
    mupdf = _import_mupdf()
    if not is_pdf(data):
        raise ValidationFailed("The file is not a valid PDF.", code="invalid_pdf")
    try:
        doc = mupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValidationFailed("The PDF could not be opened. It may be corrupted.", code="invalid_pdf") from exc
    try:
        if doc.needs_pass:
            raise ValidationFailed("Password-protected PDFs are not supported.", code="encrypted_pdf")
        if doc.page_count == 0:
            raise ValidationFailed("The PDF has no pages.", code="invalid_pdf")
        if doc.page_count > max_pages:
            raise ValidationFailed(f"The PDF has {doc.page_count} pages; the limit is {max_pages}.",
                                   code="too_many_pages")
        pages: list[RenderedPage] = []
        for index in range(doc.page_count):
            page = doc.load_page(index)
            scale = dpi / 72.0
            longest = max(page.rect.width, page.rect.height) * scale
            if longest > max_side_px:
                scale *= max_side_px / longest
            pix = page.get_pixmap(matrix=mupdf.Matrix(scale, scale), alpha=False)
            pages.append(RenderedPage(index + 1, pix.tobytes("png"), pix.width, pix.height))
        return pages
    finally:
        doc.close()


def pdf_page_count(data: bytes) -> int:
    """Validate that a PDF opens (and is not encrypted) and return its page count."""
    mupdf = _import_mupdf()
    if not is_pdf(data):
        raise ValidationFailed("The file is not a valid PDF.", code="invalid_pdf")
    try:
        doc = mupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValidationFailed("The PDF could not be opened. It may be corrupted.", code="invalid_pdf") from exc
    try:
        if doc.needs_pass:
            raise ValidationFailed("Password-protected PDFs are not supported.", code="encrypted_pdf")
        return doc.page_count
    finally:
        doc.close()
