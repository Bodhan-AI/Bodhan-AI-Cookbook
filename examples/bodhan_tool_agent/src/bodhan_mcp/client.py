#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any
import aiohttp

BODHAN_BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")

INDR_TRANSCRIPT_KEY = os.environ.get("BODHAN_TRANSCRIBE_KEY", "")
INDR_TRANSLATE_KEY = os.environ.get("BODHAN_TRANSLATE_KEY", "")
INDR_SPEAK_KEY = os.environ.get("BODHAN_SPEAK_KEY", "")
INDR_OCR_KEY = os.environ.get("BODHAN_OCR_KEY", "")

COST_PER_MINUTE_STT = 0.10
COST_PER_10K_OUTPUT_TOKENS_TRANSLATE = 0.20
COST_PER_10K_CHARS_SPEAK = 6.00
COST_PER_IMAGE_OCR = 0.20


@dataclass
class CachedResponse:
    response: Any
    cost_inr: float
    timestamp: float


class InrLedger:
    def __init__(self, max_cost: float = 20.0):
        self.total = 0.0
        self.max_cost = max_cost

    def add(self, cost: float) -> bool:
        if self.total + cost > self.max_cost:
            return False
        self.total += cost
        return True

    def remaining(self) -> float:
        return self.max_cost - self.total

    def is_exhausted(self) -> bool:
        return self.total >= self.max_cost


class BodhanCache:
    def __init__(self, cache_dir: str = ".cache/bodhan"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.semaphore = asyncio.Semaphore(3)

    def _key_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _hash(self, data: str | bytes) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def get(
        self, model: str, params: dict, input_data: str | bytes
    ) -> Optional[CachedResponse]:
        key = self._hash(
            json.dumps(
                {"model": model, "params": params, "input": input_data},
                sort_keys=True
            )
        )
        path = self._key_path(key)
        if path.exists():
            data = json.loads(path.read_text())
            return CachedResponse(**data)
        return None

    def set(
        self,
        model: str,
        params: dict,
        input_data: str | bytes,
        result: Any,
        cost_inr: float
    ) -> None:
        key = self._hash(
            json.dumps(
                {"model": model, "params": params, "input": input_data},
                sort_keys=True
            )
        )
        path = self._key_path(key)
        path.write_text(
            json.dumps({"response": result, "cost_inr": cost_inr, "timestamp": time.time()})
        )


class Client:
    def __init__(
        self, ledger: InrLedger | None = None, cache_dir: str = ".cache/bodhan"
    ):
        self.ledger = ledger or InrLedger()
        self.cache = BodhanCache(cache_dir)

    def _auth_headers(self, model: str) -> dict[str, str]:
        api_key = {
            "indic-transcribe": INDR_TRANSCRIPT_KEY,
            "indic-translate": INDR_TRANSLATE_KEY,
            "indic-speak": INDR_SPEAK_KEY,
            "indic-ocr": INDR_OCR_KEY,
        }.get(model, "")
        return {"Authorization": f"Bearer {api_key}"}

    async def request(
        self,
        model: str,
        endpoint: str,
        json_data: Optional[dict] = None,
        files: Optional[dict] = None,
        timeout: int = 60,
        max_retries: int = 3,
    ) -> tuple[Any, float]:
        cache_key_input = json.dumps(
            {"endpoint": endpoint, "json": json_data}, sort_keys=True
        )
        cached = self.cache.get(
            model, {"endpoint": endpoint, "json": json_data}, cache_key_input
        )
        if cached:
            return cached.response, cached.cost_inr

        headers = self._auth_headers(model)
        url = f"{BODHAN_BASE_URL}{endpoint}"

        await self.cache.semaphore.acquire()
        try:
            for attempt in range(max_retries):
                try:
                    timeout_obj = aiohttp.ClientTimeout(total=timeout)
                    async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                        if files:
                            data = aiohttp.FormData()
                            for name, (fname, fobj, ftype) in files.items():
                                if hasattr(fobj, 'read'):
                                    content = fobj.read()
                                    fobj.seek(0)
                                else:
                                    content = fobj
                                data.add_field(name, content, filename=fname, content_type=ftype)
                            if json_data:
                                for k, v in json_data.items():
                                    if k not in files:
                                        data.add_field(k, str(v))
                            auth_header = {"Authorization": headers["Authorization"]}
                            async with session.post(url, headers=auth_header, data=data) as resp:
                                if resp.status == 401:
                                    raise RuntimeError(f"Authentication failed for {model}")
                                if resp.status == 429:
                                    await asyncio.sleep(2 ** attempt)
                                    continue
                                if resp.status >= 500:
                                    await asyncio.sleep(2 ** attempt)
                                    continue
                                result = await resp.json()
                        else:
                            json_headers = {**headers, "Content-Type": "application/json"}
                            async with session.post(url, headers=json_headers, json=json_data) as resp:
                                if resp.status == 401:
                                    raise RuntimeError(f"Authentication failed for {model}")
                                if resp.status == 429:
                                    await asyncio.sleep(2 ** attempt)
                                    continue
                                if resp.status >= 500:
                                    await asyncio.sleep(2 ** attempt)
                                    continue
                                result = await resp.json()

                        cost = self._calc_cost(model, json_data, files, result)
                        self.cache.set(
                            model, {"endpoint": endpoint, "json": json_data},
                            cache_key_input, result, cost
                        )
                        return result, cost

                except aiohttp.ClientError:
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(2 ** attempt)

            return {}, 0.0
        finally:
            self.cache.semaphore.release()

    def _calc_cost(
        self,
        model: str,
        json_data: Optional[dict],
        files: Optional[dict] = None,
        result: Any = None
    ) -> float:
        if model == "indic-transcribe":
            if result and isinstance(result, dict):
                duration = result.get("duration", 0)
                if duration:
                    return (duration / 60.0) * COST_PER_MINUTE_STT
            return COST_PER_MINUTE_STT
        elif model == "indic-translate":
            input_chars = len(json.dumps(json_data.get("text", ""))) if json_data else 0
            return (input_chars / 10000) * COST_PER_10K_OUTPUT_TOKENS_TRANSLATE
        elif model == "indic-speak":
            input_chars = len(json.dumps(json_data.get("input", ""))) if json_data else 0
            return (input_chars / 10000) * COST_PER_10K_CHARS_SPEAK
        elif model == "indic-ocr":
            return COST_PER_IMAGE_OCR
        return 0.0


client = Client()