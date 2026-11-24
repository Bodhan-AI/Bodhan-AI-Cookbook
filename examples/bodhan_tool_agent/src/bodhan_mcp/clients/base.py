"""Base HTTP client with retry, backoff, semaphore, caching, and cost calculation."""

import asyncio
import hashlib
import json
import logging
import sys
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from bodhan_mcp import config

# Ensure logging strictly outputs to sys.stderr (Rule C7)
logger = logging.getLogger("bodhan_mcp.clients")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [bodhan_mcp.clients] %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))


def _is_retriable_error(exc: BaseException) -> bool:
    """Identify retriable errors: 429, 5xx, or network timeouts."""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


class BaseBodhanClient:
    """Manages HTTP communication to Bodhan endpoints with concurrency control and caching."""

    def __init__(self, base_url: str | None = None, max_concurrency: int = 4):
        self.base_url = (base_url or config.BODHAN_BASE).rstrip("/")
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        """Lazy-initialize an AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self) -> None:
        """Close underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def clear_cache(self) -> None:
        """Clear the in-run response cache."""
        self._cache.clear()

    def _build_cache_key(
        self,
        model: str,
        payload: dict[str, Any] | None,
        data: dict[str, Any] | None,
        files: dict[str, Any] | None,
    ) -> tuple[str, str]:
        """Produce a deterministic (model, sha256) cache key including file content."""
        hasher = hashlib.sha256()
        hasher.update(model.encode("utf-8"))
        if payload is not None:
            hasher.update(self._canonicalize_input(payload))
        if data is not None:
            hasher.update(self._canonicalize_input(data))
        if files:
            for k in sorted(files.keys()):
                hasher.update(k.encode("utf-8"))
                val = files[k]
                if isinstance(val, tuple) and len(val) >= 2:
                    file_content = val[1]
                    if isinstance(file_content, bytes):
                        hasher.update(file_content)
                    elif hasattr(file_content, "read"):
                        hasher.update(file_content.read())
                elif isinstance(val, bytes):
                    hasher.update(val)
        return (model, hasher.hexdigest())

    def _canonicalize_input(self, payload: Any) -> bytes:
        """Produce a deterministic byte representation for cache keying."""
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode("utf-8")
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")

    def _get_api_key_for_model(self, model: str) -> str:
        """Select the model-specific API key from environment."""
        model_lower = model.lower()
        if "transcribe" in model_lower or "audio" in model_lower or "stt" in model_lower:
            return config.BODHAN_TRANSCRIBE_KEY
        if "translate" in model_lower or "mt" in model_lower:
            return config.BODHAN_TRANSLATE_KEY
        if "speak" in model_lower or "tts" in model_lower:
            return config.BODHAN_SPEAK_KEY
        if "ocr" in model_lower or "vision" in model_lower:
            return config.BODHAN_OCR_KEY
        return ""

    def calculate_cost(self, model: str, metrics: dict[str, Any]) -> float:
        """Compute the cost in INR according to published prices in SPEC §6.1."""
        model_lower = model.lower()
        if "transcribe" in model_lower:
            duration_s = float(metrics.get("duration_s", 0.0))
            audio_mins = duration_s / 60.0
            return round(audio_mins * config.PRICES["transcribe_per_audio_min"], 4)
        if "translate" in model_lower:
            output_tokens = int(metrics.get("output_tokens", 0))
            return round((output_tokens / 10000.0) * config.PRICES["translate_per_10k_output_tokens"], 4)
        if "speak" in model_lower:
            chars = int(metrics.get("chars", 0))
            return round((chars / 10000.0) * config.PRICES["speak_per_10k_chars"], 4)
        if "ocr" in model_lower:
            pages = int(metrics.get("pages", 1))
            return round(pages * config.PRICES["ocr_per_image"], 4)
        return 0.0

    async def call(
        self,
        model: str,
        endpoint: str,
        *,
        payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout_s: float = 60.0,
        cost_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an API call with semaphore, retry, caching, and cost tracking."""
        # 1. Check cache (Constraint C10: same input never sent to same model twice per run)
        cache_key = self._build_cache_key(model, payload, data, files)
        cache_hash = cache_key[1]

        if cache_key in self._cache:
            logger.info("Cache hit for model=%s (sha256=%s)", model, cache_hash[:8])
            cached_result = dict(self._cache[cache_key])
            cached_result["cached"] = True
            cached_result["cost_inr"] = 0.0  # Zero spend on cache hit
            return cached_result

        # 2. Acquire semaphore and call endpoint with retry
        api_key = self._get_api_key_for_model(model)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "bodhan-mcp/0.1.0",
        }
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        @retry(
            retry=retry_if_exception(_is_retriable_error),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=0.2, max=2.0),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        async def _do_http_call() -> httpx.Response:
            client = await self.get_client()
            if files:
                resp = await client.post(
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=timeout_s,
                )
            else:
                resp = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout_s,
                )
            resp.raise_for_status()
            return resp

        async with self._semaphore:
            logger.debug("Dispatching upstream call: model=%s endpoint=%s", model, endpoint)
            resp = await _do_http_call()
            content_type = resp.headers.get("content-type", "")
            if "audio/" in content_type or "octet-stream" in content_type:
                result_data = {"audio_bytes": resp.content}
            else:
                try:
                    result_data = resp.json()
                except Exception:
                    result_data = {"content": resp.content}

        # 3. Calculate cost and store in cache
        metrics = dict(cost_metrics or {})
        cost_inr = self.calculate_cost(model, metrics)
        result_payload = {
            "data": result_data,
            "cost_inr": cost_inr,
            "cached": False,
        }
        self._cache[cache_key] = dict(result_payload)
        return result_payload
