"""Document OCR tool with multi-page PDF rasterization and Markdown extraction."""

from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import List, Optional

from PIL import Image

from ..client.client import BodhanClient
from ..models import OCRBlock, OCRResult


def _rasterize_pdf_pages(pdf_path: Path, dpi: int = 150, allow_mock_fallback: bool = False) -> List[bytes]:
    """Convert multi-page PDF into PNG image bytes per page."""
    # Attempt using pypdfium2 (pure wheel)
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(pdf_path))
        page_bytes = []
        for page in pdf:
            image = page.render(scale=dpi / 72.0).to_pil()
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            page_bytes.append(buf.getvalue())
        return page_bytes
    except ImportError:
        pass

    # Attempt using pymupdf (fitz)
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        page_bytes = []
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            page_bytes.append(pix.tobytes("png"))
        return page_bytes
    except ImportError:
        pass

    if allow_mock_fallback:
        img = Image.new("RGB", (800, 1000), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return [buf.getvalue()]

    raise RuntimeError(
        "PDF rasterization requires 'pypdfium2' or 'pymupdf'. "
        "Install with `pip install pypdfium2==4.30.0`."
    )


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
FURNITURE_LABELS = {"header", "footer", "page_number", "page-header", "page-footer", "running_header", "running_footer"}


def _normalize_image_bytes(img_path: Path) -> tuple[bytes, str]:
    """Normalize any supported image format (WebP, TIFF, BMP, PNG, JPEG) into clean PNG/JPEG bytes."""
    suffix = img_path.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(
            f"Unsupported image format '{suffix}' for OCR ({img_path.name}). "
            f"Provide a PDF or image file ({', '.join(sorted(IMAGE_SUFFIXES))})."
        )
    try:
        with Image.open(img_path) as im:
            im.verify()
    except Exception as exc:
        raise ValueError(f"File '{img_path.name}' is not a valid image: {exc}") from exc

    if suffix in (".jpg", ".jpeg"):
        return img_path.read_bytes(), "image/jpeg"
    if suffix == ".png":
        return img_path.read_bytes(), "image/png"
    with Image.open(img_path) as im:
        rgb = im.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, format="PNG")
        return buf.getvalue(), "image/png"


def _filter_furniture_blocks(blocks: List[OCRBlock], drop_furniture: bool) -> List[OCRBlock]:
    if not drop_furniture:
        return blocks
    return [b for b in blocks if b.label.strip().lower() not in FURNITURE_LABELS]


def bodhan_ocr(
    file_path: str,
    table_format: str = "markdown",
    drop_furniture: bool = True,
    client: Optional[BodhanClient] = None,
) -> OCRResult:
    """Extract text, tables, and layout blocks from images, folders of images, or multi-page PDFs using Indic-OCR."""
    if not file_path or not isinstance(file_path, str):
        raise ValueError("'file_path' must be a non-empty file or directory path string.")
    if table_format not in ("markdown", "html"):
        raise ValueError(f"Unsupported table_format '{table_format}'. Must be 'markdown' or 'html'.")
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    b_client = client or BodhanClient()

    # Folder-of-images handling
    if path.is_dir():
        img_files = sorted([p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES])
        if not img_files:
            raise ValueError(f"No image files found in directory: {file_path}")
        all_markdown = []
        all_blocks = []
        total_cost = 0.0
        for idx, img_p in enumerate(img_files, start=1):
            img_bytes, img_mime = _normalize_image_bytes(img_p)
            res = b_client.ocr_page(img_bytes, mime_type=img_mime, table_format=table_format)
            all_markdown.append(f"## Page {idx} ({img_p.name})\n\n{res.markdown}")
            for b in _filter_furniture_blocks(res.blocks, drop_furniture):
                all_blocks.append(
                    OCRBlock(order=b.order, label=b.label, bbox_xyxy=b.bbox_xyxy, conf=b.conf, text=f"[P{idx}] {b.text}")
                )
            total_cost += res.cost_inr
        return OCRResult(
            markdown="\n\n---\n\n".join(all_markdown),
            page_count=len(img_files),
            blocks=all_blocks,
            cost_inr=round(total_cost, 4),
            cached=False,
        )

    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"

    # Multi-page PDF handling
    if path.suffix.lower() == ".pdf" or mime == "application/pdf":
        page_images = _rasterize_pdf_pages(path, allow_mock_fallback=b_client.mock_mode)
        all_markdown = []
        all_blocks = []
        total_cost = 0.0

        for page_idx, img_bytes in enumerate(page_images, start=1):
            res = b_client.ocr_page(img_bytes, mime_type="image/png", table_format=table_format)
            header = f"## Page {page_idx}\n\n" if len(page_images) > 1 else ""
            all_markdown.append(f"{header}{res.markdown}")
            for b in _filter_furniture_blocks(res.blocks, drop_furniture):
                all_blocks.append(
                    OCRBlock(
                        order=b.order,
                        label=b.label,
                        bbox_xyxy=b.bbox_xyxy,
                        conf=b.conf,
                        text=f"[P{page_idx}] {b.text}",
                    )
                )
            total_cost += res.cost_inr

        return OCRResult(
            markdown="\n\n---\n\n".join(all_markdown),
            page_count=len(page_images),
            blocks=all_blocks,
            cost_inr=round(total_cost, 4),
            cached=False,
        )

    # Single Image handling (PNG, JPG, WebP, TIFF, BMP normalized to PNG/JPEG)
    image_bytes, norm_mime = _normalize_image_bytes(path)
    res = b_client.ocr_page(image_bytes, mime_type=norm_mime, table_format=table_format)
    return res.model_copy(update={"blocks": _filter_furniture_blocks(res.blocks, drop_furniture)})
