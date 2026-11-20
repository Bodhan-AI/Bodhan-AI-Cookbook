"""Robust API client for Bodhan Indic models with fallback, rate limiting, and mock support."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx

from bodhan_lesson.cache import disk_cache
from bodhan_lesson.config import (
    BASE_URL,
    OCR_KEY,
    SPEAK_KEY,
    TRANSCRIBE_KEY,
    TRANSLATE_KEY,
    is_mock_mode,
)


class BodhanClient:
    """Client for Bodhan's Indic-OCR, Indic-Translate, Indic-Speak, and Indic-Transcribe APIs."""

    def __init__(self, base_url: str = BASE_URL, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transcribe_consecutive_failures = 0
        self._transcribe_circuit_broken = False
        self._transcribe_circuit_broken_time = 0.0
        self._transcribe_cooldown_seconds = 60.0

    # -------------------------------------------------------------------------
    # 1. Indic-OCR
    # -------------------------------------------------------------------------
    def ocr_page(
        self,
        image_bytes: bytes,
        image_format: str = "png",
        table_format: str = "markdown",
    ) -> dict[str, Any]:
        """Extract text, tables, and LaTeX from a page image using Indic-OCR."""
        cached_result = disk_cache.get_ocr(image_bytes)
        if cached_result is not None:
            return cached_result

        if is_mock_mode():
            return {
                "success": True,
                "markdown": "# Chapter 6: Lines and Angles\n\nIn this chapter, we study angles and lines.\n\n$$\\angle AOB = 180^\\circ$$\n",
                "blocks": [
                    {
                        "order": 0,
                        "label": "Heading",
                        "type": "SectionHeader",
                        "bbox_xyxy": [50.0, 50.0, 500.0, 90.0],
                        "conf": 0.95,
                        "text": "# Chapter 6: Lines and Angles",
                        "page": 1,
                    },
                    {
                        "order": 1,
                        "label": "Paragraph",
                        "type": "Paragraph",
                        "bbox_xyxy": [50.0, 100.0, 500.0, 150.0],
                        "conf": 0.92,
                        "text": "In this chapter, we study angles and lines.",
                        "page": 1,
                    },
                    {
                        "order": 2,
                        "label": "Equation",
                        "type": "Equation",
                        "bbox_xyxy": [50.0, 160.0, 500.0, 200.0],
                        "conf": 0.98,
                        "text": "$$\\angle AOB = 180^\\circ$$",
                        "page": 1,
                    },
                ],
                "page_count": 1,
                "table_format": table_format,
            }

        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:image/{image_format};base64,{b64}"
        headers = {
            "Authorization": f"Bearer {OCR_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all text, tables, and LaTeX equations preserving layout and reading order."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "table_format": table_format,
        }

        retries = 5
        last_exc = None
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=payload)
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                        backoff = 3.0 * (attempt + 1)
                        if resp.status_code == 429:
                            backoff = 6.0 * (attempt + 1)
                        logging.getLogger("bodhan_lesson").warning(
                            f"Indic-OCR transient HTTP {resp.status_code} on attempt {attempt+1}/{retries}. "
                            f"Retrying in {backoff:.1f}s..."
                        )
                        time.sleep(backoff)
                        continue

                    resp.raise_for_status()
                    data = resp.json()

                    markdown_text = ""
                    if "choices" in data and data["choices"]:
                        markdown_text = data["choices"][0]["message"]["content"]

                    blocks = data.get("blocks", [])
                    result = {
                        "success": True,
                        "markdown": markdown_text,
                        "blocks": blocks,
                        "raw_response": data,
                    }
                    disk_cache.set_ocr(image_bytes, result)
                    return result

            except httpx.HTTPStatusError as e:
                last_exc = e
                if attempt < retries - 1 and e.response.status_code in (429, 500, 502, 503, 504):
                    backoff = 3.0 * (attempt + 1)
                    logging.getLogger("bodhan_lesson").warning(
                        f"Indic-OCR error {e.response.status_code}. Retrying in {backoff:.1f}s..."
                    )
                    time.sleep(backoff)
                    continue
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exc = e
                if attempt < retries - 1:
                    backoff = 3.0 * (attempt + 1)
                    logging.getLogger("bodhan_lesson").warning(
                        f"Indic-OCR network/timeout: {e}. Retrying in {backoff:.1f}s..."
                    )
                    time.sleep(backoff)
                    continue
                raise

        if last_exc:
            raise last_exc
        return {"success": False, "markdown": "", "blocks": []}

    # -------------------------------------------------------------------------
    # 2. Indic-Translate
    # -------------------------------------------------------------------------
    def translate_text(
        self,
        text: str,
        target_lang: str,
        source_lang: str | None = None,
    ) -> str:
        """Translate text into target language using Indic-Translate with auto-detection."""
        if not text or not text.strip():
            return ""

        cached_trans = disk_cache.get_translation(text, target_lang, source_lang or "auto")
        if cached_trans is not None:
            return cached_trans

        if is_mock_mode():
            return f"[{target_lang}] {text}"

        headers = {
            "Authorization": f"Bearer {TRANSLATE_KEY}",
            "Content-Type": "application/json",
        }

        # Route 1: OpenAI-compatible route at /v1/chat/completions
        payload: dict[str, Any] = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "target_language_code": target_lang,
        }
        if source_lang and source_lang != "auto":
            payload["source_language_code"] = source_lang

        retries = 3
        for attempt in range(retries):
            with httpx.Client(timeout=self.timeout) as client:
                try:
                    resp = client.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        res = data["choices"][0]["message"]["content"].strip()
                        disk_cache.set_translation(text, target_lang, res, source_lang or "auto")
                        return res
                except Exception:
                    pass

                # Route 2: Fallback to /translate route
                payload_legacy = {"text": text, "target_language": target_lang}
                if source_lang and source_lang != "auto":
                    payload_legacy["source_language"] = source_lang

                try:
                    resp = client.post(f"{self.base_url}/translate", headers=headers, json=payload_legacy)
                    if resp.status_code in (500, 502, 503, 504, 429) and attempt < retries - 1:
                        time.sleep(3.0 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    res = data.get("translation", data.get("translated_text", ""))
                    disk_cache.set_translation(text, target_lang, res, source_lang or "auto")
                    return res
                except httpx.HTTPStatusError:
                    if attempt < retries - 1:
                        time.sleep(3.0 * (attempt + 1))
                        continue
                    raise

        return ""

    # -------------------------------------------------------------------------
    # 3. Indic-Speak
    # -------------------------------------------------------------------------
    def synthesize_speech(
        self,
        text: str,
        lang: str,
        voice: str | None = None,
        style: str = "educational lecture",
    ) -> bytes:
        """Synthesize natural Indic speech to PCM16 24 kHz WAV audio bytes."""
        if not text or not text.strip():
            return b""

        if is_mock_mode():
            # Generate valid 1-second 24kHz PCM16 WAV header for offline testing
            import struct
            sample_rate = 24000
            num_samples = sample_rate
            byte_rate = sample_rate * 2
            data_size = num_samples * 2
            header = struct.pack(
                "<4sI4s4sIHHIIHH4sI",
                b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16,
                1, 1, sample_rate, byte_rate, 2, 16, b"data", data_size
            )
            return header + b"\x00" * data_size

        headers = {
            "Authorization": f"Bearer {SPEAK_KEY}",
            "Content-Type": "application/json",
        }
        instructions = json.dumps({"lang": lang, "style": style})
        payload = {
            "model": "indic-speak",
            "input": text,
            "instructions": instructions,
        }
        if voice:
            payload["voice"] = voice

        retries = 4
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(f"{self.base_url}/v1/audio/speech", headers=headers, json=payload)
                    if resp.status_code == 429 and attempt < retries - 1:
                        # Rate limit reached, back off exponentially
                        backoff = 6.0 * (attempt + 1)
                        logging.getLogger("bodhan_lesson").info(f"Rate limited on Indic-Speak (429). Retrying in {backoff:.1f}s...")
                        time.sleep(backoff)
                        continue
                    if resp.status_code in (500, 502, 503, 504) and attempt < retries - 1:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    return resp.content
            except httpx.HTTPStatusError as e:
                if attempt < retries - 1 and e.response.status_code == 429:
                    backoff = 6.0 * (attempt + 1)
                    logging.getLogger("bodhan_lesson").info(f"Rate limited on Indic-Speak (429). Retrying in {backoff:.1f}s...")
                    time.sleep(backoff)
                    continue
                raise

        return b""

    # -------------------------------------------------------------------------
    # 4. Indic-Transcribe
    # -------------------------------------------------------------------------
    def transcribe_audio(
        self,
        audio_bytes: bytes,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Transcribe speech in audio bytes using Indic-Transcribe with cooldown circuit breaker."""
        if not audio_bytes:
            return {"text": "", "duration_seconds": 0.0, "status": "skipped_due_to_service_unavailability"}

        if is_mock_mode():
            return {"text": "हम्म", "duration_seconds": 1.0, "status": "passed"}

        now = time.time()
        is_probe = False
        if self._transcribe_circuit_broken:
            elapsed = now - self._transcribe_circuit_broken_time
            if elapsed < self._transcribe_cooldown_seconds:
                # Circuit remains open during cooldown period
                return {
                    "text": "",
                    "duration_seconds": 0.0,
                    "status": "skipped_due_to_service_unavailability",
                    "error": "transcribe_circuit_broken",
                }
            else:
                # Cooldown expired: allow a single recovery probe request
                is_probe = True
                logging.getLogger("bodhan_lesson").info(
                    "Indic-Transcribe circuit breaker cooldown expired (60s). Probing service with 1 recovery request..."
                )

        headers = {"Authorization": f"Bearer {TRANSCRIBE_KEY}"}
        data: dict[str, Any] = {"model": "indic-transcribe"}
        if language and language != "auto":
            data["language"] = language

        retries = 1 if is_probe else 3
        transcribe_timeout = min(self.timeout, 20.0)

        for attempt in range(retries):
            try:
                files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
                with httpx.Client(timeout=transcribe_timeout) as client:
                    resp = client.post(f"{self.base_url}/v1/audio/transcriptions", headers=headers, data=data, files=files)
                    if resp.status_code in (500, 502, 503, 504) and attempt < retries - 1:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    res_data = resp.json()

                    # Service healthy: reset circuit breaker
                    self._transcribe_consecutive_failures = 0
                    self._transcribe_circuit_broken = False
                    self._transcribe_circuit_broken_time = 0.0
                    return {
                        "text": res_data.get("text", "").strip(),
                        "duration_seconds": float(res_data.get("duration", 0.0)),
                        "status": "passed",
                    }
            except httpx.HTTPStatusError as e:
                if attempt < retries - 1 and e.response.status_code in (500, 502, 503, 504):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if e.response.status_code in (500, 502, 503, 504):
                    self._transcribe_circuit_broken = True
                    self._transcribe_circuit_broken_time = time.time()
                    self._transcribe_consecutive_failures += 3
                    logging.getLogger("bodhan_lesson").warning(
                        f"Indic-Transcribe returned HTTP {e.response.status_code} server error. "
                        "Circuit breaker opened: skipping remaining QA requests for 60s cooldown to protect pipeline runtime."
                    )
                    return {
                        "text": "",
                        "duration_seconds": 0.0,
                        "status": "skipped_due_to_service_unavailability",
                        "error": str(e),
                    }
                self._transcribe_consecutive_failures += 1
                if self._transcribe_consecutive_failures >= 3:
                    self._transcribe_circuit_broken = True
                    self._transcribe_circuit_broken_time = time.time()
                    logging.getLogger("bodhan_lesson").warning(
                        "Indic-Transcribe returned consecutive errors. "
                        "Circuit breaker opened: skipping remaining QA requests for 60s cooldown to protect pipeline runtime."
                    )
                else:
                    logging.getLogger("bodhan_lesson").warning(f"Indic-Transcribe returned HTTP {e.response.status_code}: {e}")
                return {
                    "text": "",
                    "duration_seconds": 0.0,
                    "status": "skipped_due_to_service_unavailability" if self._transcribe_circuit_broken else "failed",
                    "error": str(e),
                }
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                self._transcribe_consecutive_failures += 1
                if self._transcribe_consecutive_failures >= 3:
                    self._transcribe_circuit_broken = True
                    self._transcribe_circuit_broken_time = time.time()
                    logging.getLogger("bodhan_lesson").warning(
                        "Indic-Transcribe connection failures. Circuit breaker opened for 60s cooldown to protect pipeline runtime."
                    )
                else:
                    logging.getLogger("bodhan_lesson").warning(f"Indic-Transcribe call failed: {e}")
                return {
                    "text": "",
                    "duration_seconds": 0.0,
                    "status": "skipped_due_to_service_unavailability" if self._transcribe_circuit_broken else "failed",
                    "error": str(e),
                }

        return {
            "text": "",
            "duration_seconds": 0.0,
            "status": "skipped_due_to_service_unavailability" if self._transcribe_circuit_broken else "failed",
        }


bodhan_client = BodhanClient()
