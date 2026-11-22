"""Stage 2: Indic-OCR client. One call per page image; raw responses are cached on disk."""

from __future__ import annotations

import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from bodhan_lesson import config
from bodhan_lesson.models import Block, Page
from bodhan_lesson.stages.ingest import PageImage
from bodhan_lesson.utils.retry import backoff_delay

log = logging.getLogger(__name__)


class OcrError(RuntimeError):
    """OCR failed for a page after all retries."""


class _BlockTooLong(Exception):
    """HTTP 502: a block exceeded max_tokens (or the image could not be decoded)."""


class IndicOcrClient:
    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        self._url = f"{config.OCR_BASE_URL}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.Client(timeout=config.OCR_TIMEOUT_S)

    def parse(self, image_path: Path) -> dict[str, Any]:
        """Return the raw OCR response for one page image."""
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        last_detail = ""
        for max_tokens in config.OCR_MAX_TOKENS_LADDER:
            try:
                return self._post(self._payload(encoded, max_tokens))
            except _BlockTooLong as exc:
                last_detail = str(exc)
                log.warning("OCR 502 at max_tokens=%d for %s; escalating", max_tokens, image_path.name)
        raise OcrError(f"{image_path.name}: HTTP 502 at every max_tokens setting. {last_detail}")

    @staticmethod
    def _payload(encoded_png: str, max_tokens: int) -> dict[str, Any]:
        return {
            "model": config.OCR_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded_png}"}}
                    ],
                }
            ],
            "table_format": config.OCR_TABLE_FORMAT,
            "max_tokens": max_tokens,
        }

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(config.OCR_MAX_ATTEMPTS):
            try:
                response = self._client.post(self._url, headers=self._headers, json=payload)
            except httpx.TransportError as exc:
                log.warning("OCR transport error: %s", exc)
                self._sleep(attempt)
                continue
            status = response.status_code
            if status == 200:
                return response.json()
            if status == 502:
                raise _BlockTooLong(response.text[:300])
            if status == 429:
                if "credit" in response.text.lower() or "balance" in response.text.lower():
                    raise OcrError("OCR credit exhausted (HTTP 429); not retrying.")
                self._sleep(attempt, response.headers.get("retry-after"))
                continue
            if status >= 500:
                self._sleep(attempt)
                continue
            raise OcrError(f"OCR request rejected (HTTP {status}): {response.text[:300]}")
        raise OcrError(f"OCR failed after {config.OCR_MAX_ATTEMPTS} attempts.")

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None = None) -> None:
        if retry_after and retry_after.isdigit():
            time.sleep(min(float(retry_after), config.OCR_BACKOFF_MAX_S))
        else:
            time.sleep(backoff_delay(attempt, config.OCR_BACKOFF_BASE_S, config.OCR_BACKOFF_MAX_S))


def page_from_response(image: PageImage, response: dict[str, Any]) -> Page:
    """Convert a raw OCR response into a Page. Blocks are sorted by `order`, never indexed by it."""
    raw_blocks = sorted(response.get("blocks", []), key=lambda b: b["order"])
    markdown = ""
    choices = response.get("choices") or []
    if choices:
        markdown = choices[0].get("message", {}).get("content", "") or ""
    return Page(
        number=image.number,
        width=image.width,
        height=image.height,
        image_path=str(image.path),
        blocks=[Block.from_ocr(image.number, raw) for raw in raw_blocks],
        markdown=markdown,
    )


def run_ocr(client: IndicOcrClient, images: list[PageImage], raw_dir: Path) -> list[Page]:
    """OCR every page, reusing cached raw responses so reruns cost nothing."""
    raw_dir.mkdir(parents=True, exist_ok=True)

    def process(image: PageImage) -> Page:
        raw_path = raw_dir / f"page_{image.number:03d}.raw.json"
        if raw_path.is_file():
            log.info("page %d: OCR cache hit", image.number)
            response = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            log.info("page %d: calling Indic-OCR", image.number)
            response = client.parse(image.path)
            raw_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        return page_from_response(image, response)

    with ThreadPoolExecutor(max_workers=config.OCR_CONCURRENCY) as pool:
        return list(pool.map(process, images))
