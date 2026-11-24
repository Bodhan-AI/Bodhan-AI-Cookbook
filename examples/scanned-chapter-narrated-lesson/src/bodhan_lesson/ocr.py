import base64
import io
import json
from pathlib import Path
import requests
from pdf2image import convert_from_path
from PIL import Image
from .config import BODHAN_BASE_URL, POPPLER_PATH


def parse_page_range(pages_arg: str, total_pages: int) -> list[int]:
    """Parse '--pages all', '1-4', or '1,3,5-7' into 1-based page numbers."""
    cleaned = pages_arg.strip().lower()
    if cleaned in ("all", "*"):
        return list(range(1, total_pages + 1))

    pages: set[int] = set()
    for part in cleaned.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start < 1 or end > total_pages or start > end:
                raise ValueError(f"Invalid page range '{part}' for PDF with {total_pages} pages.")
            pages.update(range(start, end + 1))
        else:
            page = int(part)
            if page < 1 or page > total_pages:
                raise ValueError(f"Page {page} out of bounds for PDF with {total_pages} pages.")
            pages.add(page)
    return sorted(pages)


def _image_to_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def run_ocr_on_pdf(
    pdf_path: Path,
    out_dir: Path,
    ocr_key: str,
    page_spec: str | None = None,
) -> list[dict]:
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    images = convert_from_path(str(pdf_path), dpi=200, poppler_path=POPPLER_PATH)
    total_pages = len(images)
    target_pages = (
        parse_page_range(page_spec, total_pages)
        if page_spec
        else list(range(1, total_pages + 1))
    )

    results = []
    for page_num in target_pages:
        cache_file = cache_dir / f"ocr_page_{page_num}.json"
        if cache_file.exists():
            print(f"[OCR] Page {page_num}: loaded from cache")
            results.append(json.loads(cache_file.read_text(encoding="utf-8")))
            continue

        print(f"[OCR] Page {page_num}: calling indic-ocr...")
        data_url = _image_to_data_url(images[page_num - 1])
        resp = requests.post(
            f"{BODHAN_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {ocr_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "indic-ocr",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}}
                        ],
                    }
                ],
                "table_format": "markdown",
            },
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
        page_record = {
            "page": page_num,
            "markdown": payload["choices"][0]["message"]["content"],
            "blocks": payload.get("blocks", []),
        }
        cache_file.write_text(json.dumps(page_record, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(page_record)

    return results