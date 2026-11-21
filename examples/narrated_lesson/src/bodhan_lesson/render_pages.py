"""Turn a PDF or image folder into ordered page images.

Assignment rule: treat PDFs as scans — render pages to images.
"""

from __future__ import annotations

from pathlib import Path
from shutil import copy2

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - exercised only when pymupdf missing
    fitz = None

from bodhan_lesson.constants import DEFAULT_DPI, IMAGE_SUFFIXES
from bodhan_lesson.limits import assert_input_size_ok, assert_page_count_ok

# Re-export for callers that import suffixes from this module.
__all__ = [
    "IMAGE_SUFFIXES",
    "DEFAULT_DPI",
    "list_image_files",
    "render_pdf_to_images",
    "copy_images_to_pages_dir",
    "clear_old_pages",
    "prepare_page_images",
]


def list_image_files(folder: Path) -> list[Path]:
    """Return image files in a stable sorted order."""
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    return sorted(files, key=lambda path: path.name.lower())


def render_pdf_to_images(pdf_path: Path, pages_dir: Path, dpi: int = DEFAULT_DPI) -> list[Path]:
    """Render each PDF page to a PNG file using PyMuPDF.

    Only rasterizes pages. Does not extract embedded PDF text.
    Checks page count before rendering so oversize chapters fail fast.
    """
    if fitz is None:
        raise RuntimeError(
            "Missing dependency pymupdf. Run: pip install pymupdf"
        )

    pages_dir.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise RuntimeError(f"Could not open PDF: {pdf_path}. Details: {exc}") from exc

    try:
        if doc.page_count < 1:
            raise RuntimeError(f"No pages found in PDF: {pdf_path}")

        assert_page_count_ok(doc.page_count)

        output_paths: list[Path] = []
        for index in range(doc.page_count):
            page = doc[index]
            pix = page.get_pixmap(dpi=dpi)
            out_path = pages_dir / f"page_{index + 1:03d}.png"
            pix.save(str(out_path))
            output_paths.append(out_path)
        return output_paths
    finally:
        doc.close()


def copy_images_to_pages_dir(image_files: list[Path], pages_dir: Path) -> list[Path]:
    """Copy input images into out/source/pages with steady names."""
    pages_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    for index, src in enumerate(image_files, start=1):
        out_path = pages_dir / f"page_{index:03d}{src.suffix.lower()}"
        copy2(src, out_path)
        output_paths.append(out_path)
    return output_paths


def clear_old_pages(pages_dir: Path) -> None:
    """Remove previous page_* files so reruns stay clean."""
    if not pages_dir.exists():
        return
    for old in pages_dir.glob("page_*"):
        if old.is_file():
            old.unlink()


def prepare_page_images(input_path: Path, pages_dir: Path) -> list[Path]:
    """Prepare ordered page images from a PDF file or an image folder."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    assert_input_size_ok(input_path)

    pages_dir.mkdir(parents=True, exist_ok=True)
    clear_old_pages(pages_dir)

    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise ValueError(f"Input file must be a PDF, got: {input_path.name}")
        return render_pdf_to_images(input_path, pages_dir)

    if input_path.is_dir():
        image_files = list_image_files(input_path)
        if not image_files:
            raise FileNotFoundError(
                f"No page images found in folder: {input_path}"
            )
        assert_page_count_ok(len(image_files))
        return copy_images_to_pages_dir(image_files, pages_dir)

    raise ValueError(f"Input must be a PDF file or a folder: {input_path}")
