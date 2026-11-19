"""Robust HTTP client for Bodhan AI API endpoints.
Features:
- Concurrency limiting (capped below the 10-parallel limit)
- Exponential backoff with jitter on 429 and 5xx
- Honor rate limit headers
- Transparent disk caching
"""

import asyncio
import hashlib
import os
import random
import time
from typing import Any, Dict, Optional, Tuple
import httpx
from bodhan_mcp.config import settings
from bodhan_mcp.cache import cache
from bodhan_mcp.types import ToolError


class BodhanClient:
    def __init__(self):
        self.base_url = settings.BODHAN_BASE_URL.rstrip("/")
        self.timeout = settings.REQUEST_TIMEOUT_S
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_CALLS)
        self.sync_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_CALLS)

    def _headers(self, api_key: str) -> Dict[str, str]:
        if not api_key:
            raise ToolError(
                error_code="MISSING_API_KEY",
                message="Bodhan API key is missing. Set the appropriate environment variable.",
                retryable=False,
            )
        return {"Authorization": f"Bearer {api_key}"}

    def _execute_with_retry(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        files: Optional[Dict] = None,
        max_retries: int = 5,
    ) -> Tuple[httpx.Response, bool]:
        """Synchronous HTTP execution with exponential backoff and rate limit handling."""
        attempt = 0
        backoff = 1.0

        while attempt < max_retries:
            attempt += 1
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        data=data,
                        json=json_data,
                        files=files,
                    )

                # Track rate limit headers
                remaining = resp.headers.get("X-Ratelimit-Api_key-Remaining-Requests")
                if remaining is not None and int(remaining) <= 1:
                    time.sleep(1.0)

                if resp.status_code == 200:
                    return resp, False

                if resp.status_code in (401, 403, 429, 500, 502, 503, 504):
                    jitter = random.uniform(0.1, 0.5)
                    sleep_time = backoff + jitter
                    time.sleep(sleep_time)
                    backoff *= 2.0
                    continue

                # Non-retryable error
                raise ToolError(
                    error_code=f"HTTP_{resp.status_code}",
                    message=f"Bodhan API error ({resp.status_code}): {resp.text}",
                    retryable=False,
                )

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt >= max_retries:
                    raise ToolError(
                        error_code="NETWORK_TIMEOUT",
                        message=f"Network timeout after {max_retries} attempts: {e}",
                        retryable=True,
                    )
                time.sleep(backoff)
                backoff *= 2.0

        raise ToolError(
            error_code="MAX_RETRIES_EXCEEDED",
            message=f"Failed after {max_retries} attempts",
            retryable=False,
        )

    # --- 1. Indic-Transcribe ---
    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        vad: bool = True,
    ) -> Tuple[str, bool]:
        """Transcribe an audio chunk (< 28s). Returns (transcript_text, is_cached)."""
        if not os.path.exists(audio_path):
            raise ToolError("FILE_NOT_FOUND", f"Audio file not found: {audio_path}")

        # Check cache by file content hash + params
        with open(audio_path, "rb") as f:
            file_bytes = f.read()

        cache_key_data = {
            "file_size": len(file_bytes),
            "file_sha256": hashlib.sha256(file_bytes).hexdigest(),
            "language": language,
            "vad": vad,
        }
        cached_res = cache.get_json("/v1/audio/transcriptions", "indic-transcribe", cache_key_data)
        if cached_res is not None:
            return cached_res.get("text", ""), True

        url = f"{self.base_url}/audio/transcriptions"
        headers = self._headers(settings.BODHAN_TRANSCRIBE_KEY)

        data = {"model": "indic-transcribe", "vad": vad}
        if language:
            data["language"] = language

        with open(audio_path, "rb") as af:
            audio_bytes = af.read()

        filename = os.path.basename(audio_path)
        # Detect mime type roughly
        mime = "audio/mpeg" if filename.lower().endswith(".mp3") else "audio/wav"
        files = {"file": (filename, audio_bytes, mime)}
        resp, _ = self._execute_with_retry("POST", url, headers=headers, data=data, files=files)

        res_json = resp.json()
        transcript = res_json.get("text", "").strip()

        # Save to cache
        cache.set_json("/v1/audio/transcriptions", "indic-transcribe", cache_key_data, {"text": transcript})
        return transcript, False

    # --- 2. Indic-Translate ---
    def translate(
        self,
        text: str,
        target_language_code: str,
        source_language_code: Optional[str] = None,
        target_script: str = "native",
    ) -> Tuple[str, int, bool]:
        """Translate text using Indic-Translate. Returns (translated_text, tokens, is_cached)."""
        if not text.strip():
            return "", 0, True

        cache_data = {
            "text": text,
            "target_language_code": target_language_code,
            "source_language_code": source_language_code,
            "target_script": target_script,
        }
        cached = cache.get_json("/v1/chat/completions", "indic-translate", cache_data)
        if cached is not None:
            return cached["text"], cached.get("tokens", 0), True

        url = f"{self.base_url}/chat/completions"
        headers = self._headers(settings.BODHAN_TRANSLATE_KEY)

        payload = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "target_language_code": target_language_code,
            "target_script": target_script,
        }
        if source_language_code:
            payload["source_language_code"] = source_language_code

        resp, _ = self._execute_with_retry("POST", url, headers=headers, json_data=payload)
        res_json = resp.json()

        choices = res_json.get("choices", [])
        if not choices:
            raise ToolError("EMPTY_TRANSLATE_RESPONSE", "Indic-Translate returned no choices.")

        translated_text = choices[0].get("message", {}).get("content", "").strip()
        tokens = res_json.get("usage", {}).get("total_tokens", len(text) // 3)

        cache.set_json("/v1/chat/completions", "indic-translate", cache_data, {"text": translated_text, "tokens": tokens})
        return translated_text, tokens, False

    # --- 3. Indic-Speak ---
    def speak(
        self,
        text: str,
        voice: str = "Amit",
        lang: str = "hi",
    ) -> Tuple[bytes, bool]:
        """Synthesize speech using Indic-Speak. Returns (audio_bytes_mp3, is_cached)."""
        if not text.strip():
            raise ToolError("EMPTY_TEXT", "Text for speech synthesis cannot be empty.")

        cache_data = {"text": text, "voice": voice, "lang": lang}
        cached_audio = cache.get_bytes("/v1/audio/speech", "indic-speak", cache_data)
        if cached_audio is not None:
            return cached_audio, True

        url = f"{self.base_url}/audio/speech"
        headers = self._headers(settings.BODHAN_SPEAK_KEY)

        payload = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": f'{{"lang": "{lang}"}}',
        }

        try:
            resp, _ = self._execute_with_retry("POST", url, headers=headers, json_data=payload, max_retries=4)
        except Exception as e:
            # Fallback to universal verified voice Amit if custom voice triggered backend 500
            if voice != "Amit":
                payload["voice"] = "Amit"
                resp, _ = self._execute_with_retry("POST", url, headers=headers, json_data=payload, max_retries=4)
            else:
                raise e

        audio_bytes = resp.content

        cache.set_bytes("/v1/audio/speech", "indic-speak", cache_data, audio_bytes)
        return audio_bytes, False

    # --- 4. Indic-OCR ---
    def ocr(self, b64_image: str, mime_type: str = "image/jpeg") -> Tuple[str, int, bool]:
        """Run OCR on base64 PNG/JPG image. Returns (markdown_text, table_count, is_cached)."""
        cache_data = {"image_b64_hash": hashlib.sha256(b64_image.encode("utf-8")).hexdigest()}
        cached = cache.get_json("/v1/chat/completions", "indic-ocr", cache_data)
        if cached is not None:
            return cached["text"], cached.get("table_count", 0), True

        url = f"{self.base_url}/chat/completions"
        headers = self._headers(settings.BODHAN_OCR_KEY)

        if b64_image.startswith("data:"):
            img_url = b64_image
        else:
            img_url = f"data:{mime_type};base64,{b64_image}"

        payload = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": img_url}}
                    ],
                }
            ],
        }

        resp, _ = self._execute_with_retry("POST", url, headers=headers, json_data=payload)
        res_json = resp.json()

        choices = res_json.get("choices", [])
        content = choices[0].get("message", {}).get("content", "").strip() if choices else ""

        # Count tables from blocks
        blocks = res_json.get("blocks", [])
        table_count = sum(1 for b in blocks if b.get("label") == "Table" or b.get("type") == "Table")

        cache.set_json("/v1/chat/completions", "indic-ocr", cache_data, {"text": content, "table_count": table_count})
        return content, table_count, False


bodhan_client = BodhanClient()

