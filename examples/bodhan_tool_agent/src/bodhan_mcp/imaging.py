import io
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MAX_SIDE = 2200   # shrink huge photos so the upload stays small
PDF_DPI = 170     # the notebook suggests 150-200 DPI


def to_png_bytes(img: Image.Image) -> bytes:
    """Fix phone-photo rotation, convert to RGB, shrink if huge, return PNG bytes."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    longest = max(img.size)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def load_pages(path: str, max_pages: int) -> tuple[list[bytes], int]:
    """Return (list of page images as PNG bytes, total number of pages in the file)."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_TYPES:
        with Image.open(path) as img:
            return [to_png_bytes(img)], 1
    if suffix == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(path)
        total = len(pdf)
        images = []
        for index in range(min(total, max_pages)):
            rendered = pdf[index].render(scale=PDF_DPI / 72).to_pil()
            images.append(to_png_bytes(rendered))
        return images, total
    raise ValueError(f"Unsupported file type '{suffix}'. Use a PDF, PNG, JPG, WEBP, BMP or TIFF file.")