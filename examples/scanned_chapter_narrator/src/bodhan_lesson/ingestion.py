"""Ingestion module: image-only page rendering from PDFs or image directories."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image


class DocumentIngestion:
    """Renders scanned document pages into high-resolution images without extracting PDF text."""

    def __init__(self, target_dpi: int = 300, max_dimension: int = 3500):
        self.target_dpi = target_dpi
        self.max_dimension = max_dimension

    def ingest(self, input_path: str | Path, output_pages_dir: str | Path) -> list[Path]:
        """Ingest a PDF or image folder and return sequential page image paths.
        
        Strict constraint: Never extracts embedded text from the PDF DOM.
        All pages are rasterized to pixel images.
        """
        p = Path(input_path).resolve()
        out_dir = Path(output_pages_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        if not p.exists():
            raise FileNotFoundError(f"Input path does not exist: {input_path}")

        if p.is_dir():
            # Check if directory contains a PDF file
            pdf_files = sorted(list(p.glob("*.pdf")))
            if pdf_files:
                return self._ingest_pdf(pdf_files[0], out_dir)
            return self._ingest_image_directory(p, out_dir)
        elif p.suffix.lower() == ".pdf":
            return self._ingest_pdf(p, out_dir)
        elif p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".tiff"):
            dest = out_dir / f"page_01{p.suffix.lower()}"
            img = Image.open(p)
            img.save(dest)
            return [dest]
        else:
            raise ValueError(f"Unsupported input format: {p.suffix}. Expected PDF or image directory.")

    def _ingest_pdf(self, pdf_path: Path, out_dir: Path) -> list[Path]:
        """Rasterize each PDF page to a high-resolution PNG image using pypdfium2."""
        doc = pdfium.PdfDocument(pdf_path)
        page_paths: list[Path] = []

        scale = self.target_dpi / 72.0  # 72 points per inch in PDF spec

        for page_idx in range(len(doc)):
            page_num = page_idx + 1
            page = doc[page_idx]

            # Render at target scale
            image = page.render(scale=scale).to_pil()

            # Conditional resizing only if dimensions exceed maximum bounds (preserves equations)
            width, height = image.size
            if max(width, height) > self.max_dimension:
                ratio = self.max_dimension / float(max(width, height))
                new_size = (int(width * ratio), int(height * ratio))
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            dest_path = out_dir / f"page_{page_num:02d}.png"
            image.save(dest_path, format="PNG", optimize=True)
            page_paths.append(dest_path)

        return page_paths

    def _ingest_image_directory(self, dir_path: Path, out_dir: Path) -> list[Path]:
        """Normalize, sort, and copy a folder of page images."""
        valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff"}
        image_files = sorted([
            f for f in dir_path.iterdir()
            if f.is_file() and f.suffix.lower() in valid_exts
        ])

        if not image_files:
            image_files = sorted([
                f for f in dir_path.rglob("*")
                if f.is_file() and f.suffix.lower() in valid_exts
            ])

        if not image_files:
            raise ValueError(f"No valid image files found in directory: {dir_path}")

        page_paths: list[Path] = []
        for idx, src_file in enumerate(image_files):
            page_num = idx + 1
            dest_path = out_dir / f"page_{page_num:02d}.png"
            img = Image.open(src_file)
            img.save(dest_path, format="PNG")
            page_paths.append(dest_path)

        return page_paths


document_ingestion = DocumentIngestion()


def ingest_scans_to_page_images(input_path: str | Path, output_pages_dir: str | Path) -> list[Path]:
    """Helper function to rasterize or copy document pages into images."""
    return document_ingestion.ingest(input_path, output_pages_dir)

