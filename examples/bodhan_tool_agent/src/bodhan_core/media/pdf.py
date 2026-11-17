"""
PDF processing: page counting and rendering to PNG images using pypdfium2.
"""
from __future__ import annotations

import logging
from pathlib import Path

from bodhan_core.errors import BodhanError, ErrorCode

log = logging.getLogger(__name__)


def count_pages(pdf_path: Path) -> int:
    """Return the total number of pages in a PDF document."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise BodhanError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"PDF file not found: {pdf_path}",
            how_to_fix="Ensure file exists.",
        )

    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_path)
        count = len(pdf)
        pdf.close()
        return count
    except Exception as exc:
        raise BodhanError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message=f"Could not read PDF {pdf_path.name}: {exc}",
            how_to_fix="Ensure file is a valid unencrypted PDF.",
        )


def render_pdf_pages(
    pdf_path: Path,
    out_dir: Path,
    dpi: int = 150,
) -> list[Path]:
    """
    Render every page of the PDF to a PNG image in out_dir.
    Returns list of paths to generated PNG files in page order.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        raise BodhanError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"PDF file not found: {pdf_path}",
            how_to_fix="Ensure file exists.",
        )

    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_path)
        rendered_images: list[Path] = []

        scale = dpi / 72.0  # 72 is standard PDF point resolution
        for page_idx in range(len(pdf)):
            page = pdf[page_idx]
            pil_image = page.render(scale=scale).to_pil()
            out_img_path = out_dir / f"page_{page_idx + 1:04d}.png"
            pil_image.save(out_img_path, format="PNG")
            rendered_images.append(out_img_path)

        pdf.close()
        return rendered_images
    except Exception as exc:
        raise BodhanError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message=f"Failed to render PDF pages: {exc}",
            how_to_fix="Ensure pypdfium2 and Pillow are installed and PDF is not corrupt.",
        )
