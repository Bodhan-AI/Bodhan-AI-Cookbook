"""Bodhan AI API Client with retries, exponential backoff, and concurrency control.

Communicates with https://api.bodhan.ai/v1 endpoints for:
- Indic-Transcribe (Speech-to-Text)
- Indic-Translate (Machine Translation)
- Indic-Speak (Text-to-Speech)
- Indic-OCR (Document & Image OCR)
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import random
import struct
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

from bodhan_mcp.config import SUPPORTED_LANGUAGES, config, normalize_language_code

logger = logging.getLogger("bodhan.client")


class BodhanAPIError(Exception):
    """Exception raised for Bodhan API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class BodhanClient:
    """Client for Bodhan AI Indic models."""

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None):
        self.base_url = (base_url or config.base_url).rstrip("/")
        self.timeout = timeout or config.request_timeout_sec
        self.max_retries = config.max_retries
        self._semaphore = asyncio.Semaphore(config.max_concurrency)

    def _is_mock_mode(self, key: str) -> bool:
        """Check if client should operate in deterministic mock mode."""
        if os.getenv("BODHAN_MOCK", "").lower() in ("true", "1", "yes"):
            return True
        if not key or key.startswith("mock") or key.startswith("test") or key == "your_bodhan_unified_api_key_here":
            return True
        return False

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        headers: Dict[str, str],
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Execute HTTP request with exponential backoff and jitter."""
        url = f"{self.base_url}{endpoint}"
        async with self._semaphore:
            last_err = None
            for attempt in range(self.max_retries):
                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        response = await client.request(
                            method=method,
                            url=url,
                            headers=headers,
                            params=params,
                            json=json_data,
                            files=files,
                            data=data,
                        )

                    # Success
                    if response.status_code in (200, 201):
                        return response

                    # Rate limited or server error -> backoff & retry
                    if response.status_code in (429, 500, 502, 503, 504):
                        logger.warning(
                            "Bodhan API attempt %d returned status %d: %s",
                            attempt + 1,
                            response.status_code,
                            response.text,
                        )
                    else:
                        # Client error (400, 401, 403, 404) -> fail immediately
                        raise BodhanAPIError(
                            f"Bodhan API error ({response.status_code}): {response.text}",
                            status_code=response.status_code,
                            details=response.text,
                        )

                except httpx.RequestError as e:
                    last_err = e
                    logger.warning("Network error on Bodhan API attempt %d: %s", attempt + 1, e)

                # Exponential backoff with random jitter
                backoff = (1.5 ** attempt) + random.uniform(0.1, 0.5)
                await asyncio.sleep(backoff)

            raise BodhanAPIError(
                f"Bodhan API failed after {self.max_retries} attempts: {last_err or 'Service unavailable'}"
            )

    # --------------------------------------------------------------------------
    # Indic-Transcribe (Speech-to-Text)
    # --------------------------------------------------------------------------
    async def transcribe(
        self,
        audio_path: Union[str, Path],
        language: Optional[str] = "auto",
        model_variant: str = "core",
    ) -> Dict[str, Any]:
        """Transcribe speech audio file via Indic-Transcribe.
        
        Variants: 'core' (default, native script), 'flex' (mixed-script/romanized)
        """
        key = config.get_api_key_for("transcribe")
        path = Path(audio_path).resolve()

        if self._is_mock_mode(key):
            # Deterministic mock response
            norm_lang = normalize_language_code(language or "hi") or "hi"
            mock_transcripts = {
                "hi": "नमस्ते, यह बोधन एआई इंडिक-ट्रांसक्राइब मॉडल द्वारा ट्रांसक्राइब किया गया ऑडियो पाठ है।",
                "kn": "ನಮಸ್ಕಾರ, ಇದು ಬೋಧನ್ ಎಐ ಇಂಡಿಕ್-ಟ್ರಾನ್ಸ್‌ಕ್ರೈಬ್ ಮಾದರಿಯಿಂದ ಲಿಪ್ಯಂತರಿಸಲಾದ ಪಠ್ಯವಾಗಿದೆ.",
                "ta": "வணக்கம், இது போதன் ஏஐ இண்டிக்-டிரான்ஸ்கிரைப் மூலம் பெறப்பட்ட உரை ஆகும்.",
                "te": "నమస్కారం, ఇది బోధన్ ఏఐ ద్వారా లిప్యంతరీకరించబడిన ఆడియో పాఠం.",
                "bn": "নমস্কার, এটি বোধন এআই ইন্ডিক-ট্রান্সক্রাইব দ্বারা তৈরি টেক্সট।",
                "en": "Hello, this is audio transcribed by the Bodhan AI Indic-Transcribe model.",
            }
            sample_text = mock_transcripts.get(
                norm_lang,
                f"Transcribed audio content for language '{norm_lang}' using Indic-Transcribe.",
            )
            return {
                "text": sample_text,
                "language": norm_lang,
                "model": f"indic-transcribe-{model_variant}",
                "mock": True,
            }

        headers = {"Authorization": f"Bearer {key}"}
        model_name = "indic-transcribe-flex" if model_variant == "flex" else "indic-transcribe"

        data: Dict[str, Any] = {"model": model_name}
        if language and language != "auto":
            data["language"] = normalize_language_code(language) or language

        with open(path, "rb") as f:
            files = {"file": (path.name, f.read(), "audio/wav")}

        resp = await self._request_with_retry(
            method="POST",
            endpoint="/audio/transcriptions",
            headers=headers,
            files=files,
            data=data,
        )
        return resp.json()

    # --------------------------------------------------------------------------
    # Indic-Translate (Machine Translation)
    # --------------------------------------------------------------------------
    async def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: Optional[str] = "en",
    ) -> Dict[str, Any]:
        """Translate text across Indian languages via Indic-Translate."""
        key = config.get_api_key_for("translate")
        src_norm = normalize_language_code(source_lang or "en") or "en"
        tgt_norm = normalize_language_code(target_lang)

        if not tgt_norm:
            raise BodhanAPIError(
                f"Unsupported target language '{target_lang}'. "
                f"Bodhan Indic-Translate only supports the 22 scheduled Indian languages and English."
            )

        if self._is_mock_mode(key):
            # Deterministic mock translations
            mock_translations = {
                "hi": f"अनुवाद ({tgt_norm}): यह पाठ बोधन इंडिक-ट्रांसलेट द्वारा अनुवादित किया गया है। " + text.replace("Hello", "नमस्ते").replace("Class starts at nine", "कक्षा नौ बजे शुरू होती है"),
                "kn": f"ಅನುವಾದ ({tgt_norm}): ಈ ಪಠ್ಯವನ್ನು ಬೋಧನ್ ಇಂಡಿಕ್-ಟ್ರಾನ್ಸ್‌ಲೇಟ್ ಮೂಲಕ ಅನುವಾದಿಸಲಾಗಿದೆ. " + text.replace("Hello", "ನಮಸ್ಕಾರ").replace("Class starts at nine", "ತರಗತಿಯು ಒಂಬತ್ತು ಗಂಟೆಗೆ ಪ್ರಾರಂಭವಾಗುತ್ತದೆ"),
                "ta": f"மொழிபெயர்ப்பு ({tgt_norm}): இந்த உரை போதன் இண்டிக்-டிரான்ஸ்லேட் மூலம் மொழிபெயர்க்கப்பட்டது. " + text.replace("Hello", "வணக்கம்"),
                "te": f"అనువాదం ({tgt_norm}): ఈ పాఠం బోధన్ ఇండిక్-ట్రాన్స్‌లేట్ ద్వారా అనువదించబడింది. " + text.replace("Hello", "నమస్కారం"),
                "bn": f"অনুবাদ ({tgt_norm}): এই পাঠ্যটি বোধন ইন্ডিক-ট্রান্সলেট দ্বারা অনুবাদ করা হয়েছে। " + text.replace("Hello", "নমস্কার"),
                "en": f"Translation (en): {text}",
            }
            res = mock_translations.get(
                tgt_norm,
                f"[{SUPPORTED_LANGUAGES.get(tgt_norm, tgt_norm)} Translation]: {text}",
            )
            tokens = max(1, len(res.split()) * 2)
            return {
                "translated_text": res,
                "source_language_code": src_norm,
                "target_language_code": tgt_norm,
                "usage": {"completion_tokens": tokens, "prompt_tokens": len(text.split()) * 2},
                "mock": True,
            }

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "source_language_code": src_norm,
            "target_language_code": tgt_norm,
        }

        resp = await self._request_with_retry(
            method="POST",
            endpoint="/chat/completions",
            headers=headers,
            json_data=payload,
        )
        res_json = resp.json()
        translated_text = res_json["choices"][0]["message"]["content"]
        tokens = res_json.get("usage", {}).get("completion_tokens", len(translated_text.split()))

        return {
            "translated_text": translated_text,
            "source_language_code": src_norm,
            "target_language_code": tgt_norm,
            "usage": res_json.get("usage", {"completion_tokens": tokens}),
        }

    # --------------------------------------------------------------------------
    # Indic-Speak (Text-to-Speech)
    # --------------------------------------------------------------------------
    async def speak(
        self,
        text: str,
        language: str = "hi",
        voice: str = "default",
        output_format: str = "wav",
    ) -> bytes:
        """Synthesize speech audio via Indic-Speak."""
        key = config.get_api_key_for("speak")
        norm_lang = normalize_language_code(language)
        if not norm_lang:
            raise BodhanAPIError(f"Unsupported language '{language}' for Indic-Speak.")

        if self._is_mock_mode(key):
            # Generate valid PCM WAV audio (tone/silence)
            sample_rate = 16000
            # Estimate duration: ~15 chars per second
            duration = max(0.5, len(text) / 15.0)
            num_samples = int(sample_rate * duration)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                # Generate gentle 440Hz sine wave tone
                data = bytearray()
                for i in range(num_samples):
                    sample = int(32767.0 * 0.1 * math.sin(2.0 * math.pi * 440.0 * (i / sample_rate)))
                    data.extend(struct.pack("<h", sample))
                wf.writeframes(data)
            return buf.getvalue()

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "language_code": norm_lang,
            "response_format": output_format,
        }

        resp = await self._request_with_retry(
            method="POST",
            endpoint="/audio/speech",
            headers=headers,
            json_data=payload,
        )
        return resp.content

    # --------------------------------------------------------------------------
    # Indic-OCR (Document OCR & Image Text Extraction)
    # --------------------------------------------------------------------------
    async def ocr(
        self,
        image_base64_data_url: str,
    ) -> Dict[str, Any]:
        """Extract printed/handwritten text and formatting via Indic-OCR."""
        key = config.get_api_key_for("ocr")

        if self._is_mock_mode(key):
            return {
                "text": "# Digitized Document\n\n- Section 1: Introduction\n- Section 2: Indic AI Sovereign Stack\n\nThis is extracted text from the document image processed with Bodhan Indic-OCR.",
                "model": "indic-ocr",
                "mock": True,
            }

        headers = {
            "Authorization": f"Bearer {key}",
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
                            "image_url": {"url": image_base64_data_url},
                        }
                    ],
                }
            ],
        }

        resp = await self._request_with_retry(
            method="POST",
            endpoint="/chat/completions",
            headers=headers,
            json_data=payload,
        )
        res_json = resp.json()
        extracted_text = res_json["choices"][0]["message"]["content"]
        return {"text": extracted_text, "model": "indic-ocr"}


# Global BodhanClient instance
bodhan_client = BodhanClient()
