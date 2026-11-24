"""PDF rendering utility using PyMuPDF."""

from pathlib import Path

import pymupdf  # PyMuPDF


def render_pages(pdf_path: str | Path, dpi: int = 200, out_dir: str | Path | None = None) -> list[Path]:
    """Render each page of a PDF document as a PNG image at specified DPI (default 200).

    Returns a list of image Paths in page order.
    """
    file_path = Path(pdf_path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    if out_dir:
        output_dir = Path(out_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Check if parent directory is writable; if read-only (e.g. docker :ro), fall back to system tempdir
        try:
            output_dir = file_path.parent / f"_{file_path.stem}_rendered_pages"
            output_dir.mkdir(parents=True, exist_ok=True)
            test_file = output_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
        except (PermissionError, OSError):
            import hashlib
            import tempfile
            path_hash = hashlib.sha256(str(file_path.resolve()).encode("utf-8")).hexdigest()[:8]
            output_dir = Path(tempfile.gettempdir()) / f"bodhan_rendered_pages_{file_path.stem}_{path_hash}"
            output_dir.mkdir(parents=True, exist_ok=True)

    page_paths: list[Path] = []
    zoom = dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)

    try:
        with pymupdf.open(str(file_path)) as doc:
            for page_index in range(len(doc)):
                page = doc.load_page(page_index)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                page_img = output_dir / f"page_{page_index + 1:04d}.png"
                pix.save(str(page_img))
                page_paths.append(page_img)
    except Exception as exc:
        raise ValueError(f"Failed to render PDF {file_path}: {exc}") from exc

    return page_paths
