"""Persistent disk cache for OCR extraction and translations to support resumable runs."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("bodhan_lesson")

DEFAULT_CACHE_DIR = Path(".bodhan_cache")


class DiskCache:
    """Caches expensive API calls (OCR, translation) keyed by payload hash."""

    def __init__(self, cache_dir: Path | str = DEFAULT_CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir)
        self.ocr_dir = self.cache_dir / "ocr"
        self.trans_dir = self.cache_dir / "translate"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.ocr_dir.mkdir(parents=True, exist_ok=True)
        self.trans_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _compute_hash(data: bytes | str) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def get_ocr(self, image_bytes: bytes) -> dict[str, Any] | None:
        """Retrieve cached OCR response for image bytes if present."""
        key = self._compute_hash(image_bytes)
        cache_file = self.ocr_dir / f"{key}.json"
        if cache_file.is_file():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                logger.debug(f"OCR cache hit for {key[:12]}...")
                return data
            except Exception as e:
                logger.warning(f"Failed to read OCR cache {cache_file}: {e}")
        return None

    def set_ocr(self, image_bytes: bytes, ocr_data: dict[str, Any]) -> None:
        """Store OCR response keyed by image hash."""
        key = self._compute_hash(image_bytes)
        cache_file = self.ocr_dir / f"{key}.json"
        try:
            cache_file.write_text(json.dumps(ocr_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write OCR cache {cache_file}: {e}")

    def get_translation(self, text: str, target_lang: str, source_lang: str = "en") -> str | None:
        """Retrieve cached translation text if present."""
        key = self._compute_hash(f"{source_lang}::{target_lang}::{text}")
        cache_file = self.trans_dir / f"{key}.txt"
        if cache_file.is_file():
            try:
                return cache_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    def set_translation(self, text: str, target_lang: str, translated_text: str, source_lang: str = "en") -> None:
        """Store translation keyed by text + language pair hash."""
        key = self._compute_hash(f"{source_lang}::{target_lang}::{text}")
        cache_file = self.trans_dir / f"{key}.txt"
        try:
            cache_file.write_text(translated_text, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write translation cache {cache_file}: {e}")


disk_cache = DiskCache()
