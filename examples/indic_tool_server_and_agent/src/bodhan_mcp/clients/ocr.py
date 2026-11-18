"""Async client for Indic-OCR."""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

import httpx

from bodhan_mcp.config import settings
from bodhan_mcp.core.cache import cache_get, cache_set, file_content_hash
from bodhan_mcp.core.cost import calculate_ocr_cost
from bodhan_mcp.core.errors import (
    BodhanError,
    INVALID_INPUT,
    FILE_NOT_FOUND,
    RATE_LIMITED,
    TIMEOUT,
    UPSTREAM_ERROR,
)
from bodhan_mcp.core.retry import bodhan_retry

logger = logging.getLogger(__name__)


@dataclass
class OcrResult:
    text: str
    cost: float = 0.0
    cached: bool = False


class OCRClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.ocr_key
        self.base_url = (base_url or settings.bodhan_base_url).rstrip("/")
        self.endpoint = f"{self.base_url}/chat/completions"

    @bodhan_retry
    async def ocr_image_file(self, image_path: str) -> str:
        """Run Indic-OCR on an image file path (PNG, JPG, etc.)."""
        if not os.path.exists(image_path):
            raise BodhanError(FILE_NOT_FOUND, f"Image file not found: {image_path}")

        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
        except Exception as exc:
            raise BodhanError(INVALID_INPUT, f"Failed to read image file: {exc}")

        return await self.ocr_image_bytes(image_bytes, mime_type="image/png")

    @bodhan_retry
    async def ocr_image_bytes(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        """Run Indic-OCR on raw image bytes."""
        if not image_bytes:
            return ""

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:{mime_type};base64,{b64}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        }
                    ],
                }
            ],
        }

        logger.info("Calling Indic-OCR (image_bytes=%d)", len(image_bytes))

        try:
            async with httpx.AsyncClient(timeout=settings.api_timeout_seconds) as client:
                resp = await client.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                )

            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                logger.info("OCR success (chars=%d)", len(content))
                return content
            elif resp.status_code == 429:
                raise BodhanError(RATE_LIMITED, "OCR API rate limit exceeded", retryable=True)
            elif resp.status_code in (408, 504):
                raise BodhanError(TIMEOUT, f"OCR API timed out: {resp.text}", retryable=True)
            elif resp.status_code >= 500:
                raise BodhanError(UPSTREAM_ERROR, f"OCR API server error ({resp.status_code}): {resp.text}", retryable=True)
            else:
                raise BodhanError(INVALID_INPUT, f"OCR API error ({resp.status_code}): {resp.text}", retryable=False)

        except httpx.TimeoutException as exc:
            raise BodhanError(TIMEOUT, f"OCR request timed out: {exc}", retryable=True)
        except httpx.RequestError as exc:
            raise BodhanError(UPSTREAM_ERROR, f"OCR network request error: {exc}", retryable=True)

    async def extract_text(self, image_path: str) -> OcrResult:
        """High-level method to extract text from image with caching and cost calculation."""
        if not os.path.exists(image_path):
            raise BodhanError(FILE_NOT_FOUND, f"Image file not found: {image_path}")

        fhash = file_content_hash(image_path)
        cached = cache_get("indic-ocr", {}, fhash)
        if cached:
            return OcrResult(text=cached if isinstance(cached, str) else cached.get("text", ""), cost=0.0, cached=True)

        text = await self.ocr_image_file(image_path)
        cost = calculate_ocr_cost(1)
        cache_set("indic-ocr", {}, fhash, {"text": text})

        return OcrResult(text=text, cost=cost, cached=False)
