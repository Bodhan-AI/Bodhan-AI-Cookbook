"""Run Indic-OCR on each page image and save Markdown + blocks.

Follows the Bodhan Document OCR API: one PNG/JPEG per request, no text prompt.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Any

from bodhan_lesson.bodhan_client import get_ocr_api_key, post_json
from bodhan_lesson.constants import IMAGE_SUFFIXES
from bodhan_lesson.progress import done, item, stage

OCR_PATH = "/v1/chat/completions"
DEFAULT_TABLE_FORMAT = "html"
DEFAULT_MAX_TOKENS = 2048
TRUNCATION_MAX_TOKENS = 4096


def list_page_images(pages_dir: Path) -> list[Path]:
    """Return page_* image files in name order (skips .md / .ocr.json)."""
    files = [
        path
        for path in pages_dir.iterdir()
        if path.is_file()
        and path.name.startswith("page_")
        and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    return sorted(files, key=lambda path: path.name.lower())


def load_existing_ocr_pages(pages_dir: Path) -> list[dict[str, Any]]:
    """Build OCR page summaries from saved page_*.ocr.json (no API calls).

    Used when ``--from`` starts after OCR so layout+ can re-run without OCR credits.
    """
    if not pages_dir.is_dir():
        raise FileNotFoundError(f"Pages directory not found: {pages_dir}")

    ocr_files = sorted(
        [
            path
            for path in pages_dir.iterdir()
            if path.is_file() and path.name.startswith("page_") and path.name.endswith(".ocr.json")
        ],
        key=lambda path: path.name.lower(),
    )
    if not ocr_files:
        raise FileNotFoundError(
            f"No page_*.ocr.json under {pages_dir}. "
            "Run a full pipeline once (no --from), or --from ocr."
        )

    results: list[dict[str, Any]] = []
    for ocr_path in ocr_files:
        stem = ocr_path.name[: -len(".ocr.json")]
        try:
            artifact = json.loads(ocr_path.read_text(encoding="utf-8"))
            blocks = artifact.get("blocks") or []
            if not isinstance(blocks, list):
                raise ValueError("blocks must be a list")
            page_name = artifact.get("page_image") or f"{stem}.png"
            results.append(
                {
                    "page": page_name,
                    "stem": stem,
                    "status": "ok",
                    "block_count": len(blocks),
                    "md_path": str(pages_dir / f"{stem}.md"),
                    "ocr_json_path": str(ocr_path),
                    "usage": artifact.get("usage") or {},
                    "error": None,
                    "latency_s": 0.0,
                    "retries": 0,
                    "reused": True,
                }
            )
        except Exception as exc:  # noqa: BLE001 — isolate per page
            results.append(
                {
                    "page": f"{stem}.png",
                    "stem": stem,
                    "status": "error",
                    "block_count": 0,
                    "md_path": None,
                    "ocr_json_path": str(ocr_path),
                    "usage": {},
                    "error": str(exc),
                    "latency_s": 0.0,
                    "retries": 0,
                    "reused": True,
                }
            )
    return results


def build_ocr_request_body(
    image_path: Path,
    *,
    table_format: str = DEFAULT_TABLE_FORMAT,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Build the exact JSON body the Document OCR API expects."""
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    if mime not in {"image/png", "image/jpeg"}:
        raise ValueError(f"OCR accepts PNG/JPEG only, got {mime} for {image_path.name}")

    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    body: dict[str, Any] = {
        "model": "indic-ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                ],
            }
        ],
        "table_format": table_format,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    return body


def extract_ocr_fields(response: dict[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Pull Markdown content, layout blocks, and usage from an OCR response."""
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("OCR response had no choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise RuntimeError("OCR response missing message.content")
    blocks = response.get("blocks") or []
    if not isinstance(blocks, list):
        raise RuntimeError("OCR response blocks must be a list")
    usage = response.get("usage") or {}
    return content, blocks, usage


def ocr_page(
    image_path: Path,
    *,
    api_key: str | None = None,
    table_format: str = DEFAULT_TABLE_FORMAT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Call indic-ocr for one page image. Retries once with higher max_tokens on 502."""
    key = api_key or get_ocr_api_key()
    body = build_ocr_request_body(
        image_path, table_format=table_format, max_tokens=max_tokens
    )
    try:
        return post_json(OCR_PATH, body, api_key=key)
    except RuntimeError as exc:
        # Large tables often need a higher per-block token budget.
        if "502" in str(exc) and max_tokens < TRUNCATION_MAX_TOKENS:
            body = build_ocr_request_body(
                image_path,
                table_format=table_format,
                max_tokens=TRUNCATION_MAX_TOKENS,
            )
            return post_json(OCR_PATH, body, api_key=key)
        raise


def save_ocr_artifacts(
    image_path: Path,
    response: dict[str, Any],
) -> dict[str, Any]:
    """Write page_NNN.md and page_NNN.ocr.json next to the page image."""
    content, blocks, usage = extract_ocr_fields(response)
    stem = image_path.stem
    pages_dir = image_path.parent

    md_path = pages_dir / f"{stem}.md"
    json_path = pages_dir / f"{stem}.ocr.json"

    md_path.write_text(content + ("\n" if not content.endswith("\n") else ""), encoding="utf-8")

    artifact = {
        "page_image": image_path.name,
        "content": content,
        "blocks": blocks,
        "usage": usage,
        "model": response.get("model", "indic-ocr"),
        "id": response.get("id"),
    }
    json_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "page": image_path.name,
        "stem": stem,
        "status": "ok",
        "block_count": len(blocks),
        "md_path": str(md_path),
        "ocr_json_path": str(json_path),
        "usage": usage,
        "error": None,
    }


def run_ocr_on_pages(
    pages_dir: Path,
    *,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """OCR every page image. One failure does not stop the rest."""
    images = list_page_images(pages_dir)
    if not images:
        raise FileNotFoundError(f"No page images found in {pages_dir}")

    key = api_key or get_ocr_api_key()
    results: list[dict[str, Any]] = []
    total = len(images)
    stage("ocr", f"{total} page(s)")

    for index, image_path in enumerate(images, start=1):
        item("ocr", index, total, image_path.name)
        started = time.perf_counter()
        try:
            response = ocr_page(image_path, api_key=key)
            summary = save_ocr_artifacts(image_path, response)
            summary["latency_s"] = round(time.perf_counter() - started, 3)
            summary["retries"] = 0
            results.append(summary)
            item("ocr", index, total, image_path.name, "ok")
        except Exception as exc:  # noqa: BLE001 — isolate per page
            results.append(
                {
                    "page": image_path.name,
                    "stem": image_path.stem,
                    "status": "error",
                    "block_count": 0,
                    "md_path": None,
                    "ocr_json_path": None,
                    "usage": {},
                    "error": str(exc),
                    "latency_s": round(time.perf_counter() - started, 3),
                    "retries": 0,
                }
            )
            item("ocr", index, total, image_path.name, f"error: {exc}")
    ok = sum(1 for row in results if row.get("status") == "ok")
    done("ocr", ok=ok, failed=len(results) - ok)
    return results
