"""Stage 1: turn a PDF or a folder of page images into per-page OCR records with flags."""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from .client import BodhanClient
from .page_split import merge_halves, split_halves

IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
MAX_SIDE = 2400          # longest image side sent to OCR, in pixels
LOW_CONF = 0.60          # plain-text blocks below this confidence are flagged
LOW_CONF_MATH = 0.50     # math-heavy blocks score lower but read fine, so they get a lower bar
PDF_DPI = 200
MAX_SPLIT_DEPTH = 2      # a page may be cut in halves twice (down to quarters)
NO_TEXT_LABELS = {"Diagram", "Figure", "Image", "Picture"}


def natural_key(path: Path):
    """Sort page_2 before page_10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def prepare_image(src: Path, dest: Path) -> Path:
    """Fix phone-photo rotation, drop alpha, cap the size and save as JPEG."""
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    img.save(dest, "JPEG", quality=92)
    return dest


def collect_pages(input_path: Path, work_dir: Path, max_pages: int | None = None) -> list[Path]:
    """Return prepared page images, in order, for a PDF or a folder of images."""
    work_dir.mkdir(parents=True, exist_ok=True)
    if input_path.is_dir():
        sources = sorted((p for p in input_path.iterdir() if p.suffix.lower() in IMAGE_EXTS), key=natural_key)
    elif input_path.suffix.lower() == ".pdf":
        from pdf2image import convert_from_path

        raw_dir = work_dir / "raw"
        raw_dir.mkdir(exist_ok=True)
        sources = [Path(p) for p in convert_from_path(
            str(input_path), dpi=PDF_DPI, fmt="png", output_folder=str(raw_dir), paths_only=True, output_file="page")]
        sources.sort(key=natural_key)
    else:
        raise ValueError(f"Input must be a PDF or a folder of PNG/JPG images: {input_path}")
    if not sources:
        raise ValueError(f"No pages found in {input_path}")
    if max_pages:
        sources = sources[:max_pages]
    return [prepare_image(src, work_dir / f"page_{i:03d}.jpg") for i, src in enumerate(sources, start=1)]


def block_flags(block: dict) -> list[str]:
    """Reasons a block should not be translated blindly."""
    if block.get("label") == "Failed-region":
        return ["ocr_failed_region"]
    if block.get("label") in NO_TEXT_LABELS:
        return []                      # diagrams hold no text, so there is nothing to doubt
    text = (block.get("text") or "").strip()
    if not text:
        return ["empty_text"]
    flags = []
    conf = block.get("conf")
    limit = LOW_CONF_MATH if "$" in text else LOW_CONF
    if conf is not None and conf < limit:
        flags.append(f"low_confidence:{conf:.2f}")
    if "\ufffd" in text:
        flags.append("replacement_character")
    if re.search(r"([^\s.\-_=*~])\1{5,}", text):   # a repeated glyph, not spacing or dotted lines
        flags.append("repeated_character_run")
    if len(text) >= 8:
        odd = sum(1 for c in text if not (c.isalnum() or c.isspace() or c in ".,;:!?()[]{}'\"-+=/%<>°|$\\^_*&।"))
        if odd / len(text) > 0.30:
            flags.append("many_unusual_symbols")
    return flags


def read_region(client: BodhanClient, image: Path, depth: int = 0) -> tuple[dict, list[str]]:
    """OCR an image. When the recognizer reports a truncated block, retry with a larger limit,
    then cut the image in two and read each half. A region that still fails becomes a flagged
    placeholder block, so one bad area never costs the whole page.

    Returns (result, notes).
    """
    limits = (None, 8192) if depth == 0 else (None,)
    for max_tokens in limits:
        try:
            return client.ocr_page(image, max_tokens=max_tokens), []
        except Exception as exc:
            if "truncated" not in str(exc).lower():
                raise                      # a different problem, so do not paper over it
    with Image.open(image) as im:
        w, h = im.size
    if depth >= MAX_SPLIT_DEPTH:
        gap = {"order": 0, "label": "Failed-region", "conf": 0.0, "bbox_xyxy": [0, 0, w, h], "text": ""}
        return {"markdown": "", "blocks": [gap]}, [f"region_failed:{image.stem}"]
    top, bottom, cut = split_halves(image)
    r_top, n_top = read_region(client, top, depth + 1)
    r_bottom, n_bottom = read_region(client, bottom, depth + 1)
    return merge_halves(r_top, r_bottom, cut), [f"split:{image.stem}"] + n_top + n_bottom


def ocr_one(client: BodhanClient, page_no: int, image: Path) -> dict:
    """OCR one page. A failure becomes a record, not an exception."""
    try:
        result, notes = read_region(client, image)
    except Exception as exc:  # one bad page must not stop the run
        return {"page": page_no, "image": image.name, "status": "failed", "error": str(exc)}
    blocks = [{**b, "flags": block_flags(b)} for b in result["blocks"]]
    with Image.open(image) as im:
        width, height = im.size
    record = {"page": page_no, "image": image.name, "status": "ok", "width": width, "height": height,
              "markdown": result["markdown"], "blocks": blocks,
              "flagged_blocks": sum(1 for b in blocks if b["flags"])}
    if notes:
        record["notes"] = notes
    return record


def run_ocr(client: BodhanClient, images: list[Path], pages_dir: Path, workers: int = 2) -> list[dict]:
    """OCR all pages with a concurrency limit and write one JSON file per page."""
    pages_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(lambda item: ocr_one(client, item[0], item[1]), enumerate(images, start=1)))
    for i, rec in enumerate(records):          # second pass, one page at a time
        if rec["status"] == "failed":
            records[i] = ocr_one(client, rec["page"], images[i])
    for rec in records:
        (pages_dir / f"page_{rec['page']:03d}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the OCR stage on its own.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--max-pages", type=int, default=None)
    args = ap.parse_args()

    out = Path(args.out)
    client = BodhanClient()
    images = collect_pages(Path(args.input), out / "work" / "page_images", args.max_pages)
    records = run_ocr(client, images, out / "source" / "pages")
    failed = 0
    for r in records:
        if r["status"] == "ok":
            labels = sorted({b["label"] for b in r["blocks"]})
            extra = f" notes={r['notes']}" if r.get("notes") else ""
            print(f"page {r['page']:2}: {len(r['blocks'])} blocks, {r['flagged_blocks']} flagged, labels={labels}{extra}")
        else:
            failed += 1
            print(f"page {r['page']:2}: FAILED {r['error']}")
    print(json.dumps(client.costs.summary(), indent=2))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()