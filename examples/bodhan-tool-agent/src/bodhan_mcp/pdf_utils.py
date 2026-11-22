"""PDF conversion utilities for multi-page document OCR.

Converts multi-page PDF documents into page PNG images for Indic-OCR processing.
Uses pypdfium2 (permissive Apache-2.0 / BSD-3 license) when available.
"""

from __future__ import annotations

from pathlib import Path


def render_pdf_to_images(
    pdf_path: Path | str,
    output_dir: Path | str | None = None,
    dpi: int = 200,
) -> list[Path]:
    """Renders all pages of a PDF into PNG images.

    Requires pypdfium2. If not installed, raises an actionable error with installation guidance.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {path}")

    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "PDF rendering requires 'pypdfium2' (permissive Apache-2.0 / BSD-3 license). "
            "Please approve adding 'pypdfium2>=4.30.0' to dependencies."
        ) from e

    if output_dir is None:
        target_dir = path.parent / f"{path.stem}_pages"
    else:
        target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(path))
    image_paths: list[Path] = []

    scale = dpi / 72.0
    for page_idx, page in enumerate(pdf):
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()
        out_image_path = target_dir / f"page_{page_idx + 1:03d}.png"
        pil_image.save(out_image_path, format="PNG")
        image_paths.append(out_image_path)

    return image_paths
