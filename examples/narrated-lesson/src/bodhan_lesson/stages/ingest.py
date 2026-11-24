"""Stage 1: turn a PDF (or a folder of images) into one PNG per page.

The PDF text layer is deliberately never read: pages are rasterised and sent
through OCR as if they were scans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class PageImage:
    number: int  # 1-based
    path: Path
    width: int
    height: int


def _natural_key(path: Path) -> list[object]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def ingest(source: Path, out_dir: Path, *, dpi: int, max_pages: int | None) -> list[PageImage]:
    """Render up to `max_pages` pages of `source` into `out_dir` as PNG."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        return _ingest_images(source, out_dir, max_pages)
    if source.suffix.lower() == ".pdf":
        return _ingest_pdf(source, out_dir, dpi, max_pages)
    raise ValueError(f"Unsupported input {source}: expected a PDF or a folder of PNG/JPG images.")


def _ingest_pdf(pdf: Path, out_dir: Path, dpi: int, max_pages: int | None) -> list[PageImage]:
    pages: list[PageImage] = []
    with pymupdf.open(pdf) as doc:
        count = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
        for index in range(count):
            pixmap = doc[index].get_pixmap(dpi=dpi, alpha=False)
            path = out_dir / f"page_{index + 1:03d}.png"
            pixmap.save(path)
            pages.append(PageImage(index + 1, path, pixmap.width, pixmap.height))
    return pages


def _ingest_images(folder: Path, out_dir: Path, max_pages: int | None) -> list[PageImage]:
    files = sorted((p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES), key=_natural_key)
    if max_pages is not None:
        files = files[:max_pages]
    pages: list[PageImage] = []
    for index, file in enumerate(files):
        with Image.open(file) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            path = out_dir / f"page_{index + 1:03d}.png"
            image.save(path)
            pages.append(PageImage(index + 1, path, image.width, image.height))
    return pages
