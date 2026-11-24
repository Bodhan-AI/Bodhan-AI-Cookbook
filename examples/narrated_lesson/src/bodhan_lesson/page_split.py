"""Fallback for pages the OCR model refuses whole: read the top and bottom halves separately."""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def split_halves(image: Path) -> tuple[Path, Path, int]:
    """Cut a page image near its vertical middle, along its whitest row.

    Returns (top_path, bottom_path, cut_y). Cutting along the whitest row between 40% and 60%
    of the height avoids slicing through a line of text.
    """
    img = Image.open(image).convert("RGB")
    w, h = img.size
    rows = img.convert("L").resize((1, h), Image.Resampling.BOX)   # average brightness of each row
    lo, hi = int(h * 0.40), int(h * 0.60)
    cut = max(range(lo, hi), key=lambda y: rows.getpixel((0, y)))
    top_path = image.with_name(f"{image.stem}_top.jpg")
    bottom_path = image.with_name(f"{image.stem}_bottom.jpg")
    img.crop((0, 0, w, cut)).save(top_path, "JPEG", quality=92)
    img.crop((0, cut, w, h)).save(bottom_path, "JPEG", quality=92)
    return top_path, bottom_path, cut


def merge_halves(top: dict, bottom: dict, cut: int) -> dict:
    """Combine two OCR results into one page result, shifting the bottom half back into place."""
    blocks = list(top["blocks"])
    offset = max((b.get("order", 0) for b in blocks), default=-1) + 1
    for b in bottom["blocks"]:
        x1, y1, x2, y2 = b["bbox_xyxy"]
        blocks.append({**b, "bbox_xyxy": [x1, y1 + cut, x2, y2 + cut], "order": b.get("order", 0) + offset})
    markdown = (top["markdown"].rstrip() + "\n\n" + bottom["markdown"].lstrip()).strip()
    return {"markdown": markdown, "blocks": blocks}