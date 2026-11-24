"""Discover ordered page descriptors and render pixels on demand."""

import re
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from ..models import Page

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
PDF_DPI = 200


def _natural_key(path: Path) -> tuple[tuple[str | int, ...], str, str]:
    folded = path.name.casefold()
    chunks = re.split(r"([0-9]+)", folded)
    natural = tuple(int(chunk) if part % 2 else chunk for part, chunk in enumerate(chunks))
    return natural, folded, path.name


def discover_pages(path: Path) -> tuple[Page, ...]:
    if path.is_dir():
        try:
            images = sorted(
                (item for item in path.iterdir()
                 if item.suffix.lower() in IMAGE_EXTENSIONS and item.is_file()),
                key=_natural_key,
            )
        except OSError as error:
            raise ValueError(f"Cannot list input folder {path}: {error.strerror}") from None
        if not images:
            raise ValueError(f"Input folder contains no PNG or JPEG pages: {path}")
        return tuple(Page(index, image) for index, image in enumerate(images, start=1))

    if path.is_file() and path.suffix.lower() == ".pdf":
        try:
            with pdfium.PdfDocument(path) as document:
                count = len(document)
        except (OSError, pdfium.PdfiumError) as error:
            raise ValueError(f"Cannot open input PDF {path}: {error}") from None
        if not count:
            raise ValueError(f"Input PDF has no pages: {path}")
        return tuple(Page(offset + 1, path, offset) for offset in range(count))

    raise ValueError(f"Expected an existing PNG/JPEG folder or PDF file, got {path}")


def render_page(page: Page) -> Image.Image:
    """Return independent pixels, without opening a PDF text page."""
    if page.pdf_offset is None:
        with Image.open(page.path, formats=["PNG", "JPEG"]) as image:
            return image.copy()

    with pdfium.PdfDocument(page.path) as document:
        pdf_page = document[page.pdf_offset]
        try:
            bitmap = pdf_page.render(scale=PDF_DPI / 72, fill_color=(255, 255, 255, 255))
            try:
                return bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            pdf_page.close()


def rgb_pixels(image: Image.Image) -> Image.Image:
    """Return an opaque RGB copy of the page on white, with metadata removed."""
    if "A" in image.getbands() or "transparency" in image.info or image.mode == "PA":
        with image.convert("RGBA") as pixels:
            background = Image.new("RGB", image.size, "white")
            background.paste(pixels, mask=pixels.getchannel("A"))
        return background
    pixels = image.convert("RGB")
    pixels.info.clear()
    return pixels


def png_bytes(image: Image.Image) -> bytes:
    """Encode canonical page pixels without source metadata."""
    with BytesIO() as buffer:
        with rgb_pixels(image) as pixels:
            pixels.save(buffer, format="PNG")
        return buffer.getvalue()
