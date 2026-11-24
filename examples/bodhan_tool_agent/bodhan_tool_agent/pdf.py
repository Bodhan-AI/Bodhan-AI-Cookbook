from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import fitz

MAX_PDF_PAGES = 50


def max_pdf_pages() -> int:
    raw = os.environ.get("BODHAN_MAX_PDF_PAGES")
    if raw is None:
        return MAX_PDF_PAGES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise PDFProcessingError(
            "BODHAN_MAX_PDF_PAGES must be a positive integer"
        ) from None
    if value <= 0:
        raise PDFProcessingError(
            "BODHAN_MAX_PDF_PAGES must be a positive integer"
        )
    return value


class PDFProcessingError(ValueError):
    """Raised when a PDF cannot be validated or rendered safely."""


@dataclass(frozen=True)
class PDFPage:
    number: int
    path: Path


def pdf_page_count(path: str | Path) -> int:
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.is_file():
        raise PDFProcessingError(f"PDF file does not exist: {resolved}")
    if resolved.stat().st_size == 0:
        raise PDFProcessingError("PDF file is empty")

    try:
        document = fitz.open(str(resolved))
    except Exception as exc:
        raise PDFProcessingError("PDF file is corrupt or unreadable") from exc

    try:
        if document.is_encrypted:
            raise PDFProcessingError(
                "Encrypted/password-protected PDFs are not supported"
            )
        count = document.page_count
        if count <= 0:
            raise PDFProcessingError("PDF contains no pages")
        limit = max_pdf_pages()
        if count > limit:
            raise PDFProcessingError(
                f"PDF exceeds the configured {limit}-page limit"
            )
        return count
    finally:
        document.close()


@contextmanager
def render_pdf_pages(
    path: str | Path,
    *,
    dpi: int = 150,
    temp_root: str | Path | None = None,
) -> Iterator[list[PDFPage]]:
    resolved = Path(path).expanduser().resolve(strict=False)
    count = pdf_page_count(resolved)
    temp_root_path = Path(temp_root).resolve() if temp_root is not None else None
    if temp_root_path is not None:
        temp_root_path.mkdir(parents=True, exist_ok=True)

    try:
        document = fitz.open(str(resolved))
    except Exception as exc:
        raise PDFProcessingError("PDF file is corrupt or unreadable") from exc

    try:
        if document.is_encrypted:
            raise PDFProcessingError(
                "Encrypted/password-protected PDFs are not supported"
            )

        with tempfile.TemporaryDirectory(
            prefix="bodhan-pdf-",
            dir=temp_root_path,
        ) as temp_dir:
            temp_path = Path(temp_dir)
            pages: list[PDFPage] = []
            try:
                zoom = dpi / 72.0
                matrix = fitz.Matrix(zoom, zoom)
                for index in range(count):
                    page = document[index]
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                    rendered = temp_path / f"page-{index + 1:04d}.png"
                    pixmap.save(str(rendered))
                    pages.append(PDFPage(index + 1, rendered))
                yield pages
            finally:
                pages.clear()
    finally:
        document.close()
