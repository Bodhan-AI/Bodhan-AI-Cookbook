"""
Bodhan API Client with Content-Hashed Disk Caching, Cost Metering, and Auto-Retries.

Supports both:
1. Local cluster / GPU gateway (e.g. http://192.168.1.7:8000/v1)
2. Official hosted Bodhan API (https://api.bodhan.ai/v1) with per-model API keys.
"""

import hashlib
import json
import logging
import os
import shutil
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

from bodhan_mcp.config import (
    BODHAN_BASE_URL,
    BODHAN_OCR_KEY,
    BODHAN_SPEAK_KEY,
    BODHAN_TRANSCRIBE_KEY,
    BODHAN_TRANSLATE_KEY,
    CACHE_DIR,
    DEFAULT_TIMEOUT_SEC,
    INDIC_SPEAK_SAMPLE_VOICES,
    PRICING_INR,
    is_language_supported,
    normalize_lang_code,
)

logger = logging.getLogger("bodhan.client")


# ---------------------------------------------------------------------------
# Custom Exceptions for Agentic Honesty and Error Handling
# ---------------------------------------------------------------------------
class BodhanError(Exception):
    """Base exception for all Bodhan client operations."""
    pass


class BodhanUnsupportedLanguageError(BodhanError):
    """Raised when an unsupported language is requested (e.g. French)."""
    def __init__(self, language: str, reason: str = ""):
        self.language = language
        msg = f"Language '{language}' is not supported by Bodhan Indic models. {reason}".strip()
        super().__init__(msg)


class BodhanAPIError(BodhanError):
    """Raised when the Bodhan API returns an error response."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Bodhan API error (HTTP {status_code}): {message}")


class BodhanRateLimitError(BodhanAPIError):
    """Raised on HTTP 429 Too Many Requests."""
    pass


class BodhanTimeoutError(BodhanError):
    """Raised when an API request times out."""
    pass


# ---------------------------------------------------------------------------
# Core Client Implementation
# ---------------------------------------------------------------------------
class BodhanClient:
    """
    Unified client for Bodhan's Indic models with disk caching,
    resilient retries, and accurate ₹ INR metering.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        transcribe_key: Optional[str] = None,
        translate_key: Optional[str] = None,
        speak_key: Optional[str] = None,
        ocr_key: Optional[str] = None,
        cache_dir: Optional[Union[str, Path]] = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        max_retries: int = 3,
    ):
        self.base_url = (base_url or BODHAN_BASE_URL).rstrip("/")
        self.transcribe_key = transcribe_key or BODHAN_TRANSCRIBE_KEY
        self.translate_key = translate_key or BODHAN_TRANSLATE_KEY
        self.speak_key = speak_key or BODHAN_SPEAK_KEY
        self.ocr_key = ocr_key or BODHAN_OCR_KEY

        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.timeout = timeout
        self.max_retries = max_retries

        # Cost tracking
        self.total_cost_inr: float = 0.0
        self.total_saved_cost_inr: float = 0.0
        self.history: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # Helper: Headers & Auth
    # -----------------------------------------------------------------------
    def _get_headers(self, model_key: Optional[str] = None) -> Dict[str, str]:
        headers = {"User-Agent": "Bodhan-MCP-Client/1.0"}
        if model_key:
            headers["Authorization"] = f"Bearer {model_key}"
        return headers

    # -----------------------------------------------------------------------
    # Helper: Content-Hashed Caching
    # -----------------------------------------------------------------------
    def _hash_file(self, file_path: Union[str, Path]) -> str:
        """Returns SHA-256 hash of a file's contents."""
        hasher = hashlib.sha256()
        path = Path(file_path)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _compute_cache_key(self, op: str, params: Dict[str, Any]) -> str:
        """Computes a deterministic cache key from operation and parameters."""
        serialized = json.dumps({"op": op, "params": params}, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _read_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Reads a cached JSON response if it exists."""
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data
            except Exception as e:
                logger.warning(f"Failed to read cache file {cache_path}: {e}")
        return None

    def _write_cache(self, cache_key: str, data: Dict[str, Any]):
        """Writes response data to disk cache."""
        cache_path = self.cache_dir / f"{cache_key}.json"
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write cache file {cache_path}: {e}")

    # -----------------------------------------------------------------------
    # Helper: Resilient Request with Exponential Backoff
    # -----------------------------------------------------------------------
    def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, Any]] = None,
        json_payload: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{endpoint}"
        last_exception = None

        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        data=data,
                        json=json_payload,
                        files=files,
                    )

                if response.status_code == 200:
                    return response

                # Transient server errors: 500, 502, 503, 504, 429
                if response.status_code in (429, 500, 502, 503, 504):
                    wait_time = 1.5 ** attempt
                    logger.warning(
                        f"Request to {url} returned HTTP {response.status_code}. "
                        f"Retrying in {wait_time:.1f}s (attempt {attempt}/{self.max_retries})..."
                    )
                    time.sleep(wait_time)
                    continue

                # Non-retryable client error (e.g. 400 Bad Request, 404 Not Found)
                raise BodhanAPIError(response.status_code, response.text)

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as net_err:
                last_exception = net_err
                wait_time = 1.5 ** attempt
                logger.warning(
                    f"Network error on {url}: {net_err}. "
                    f"Retrying in {wait_time:.1f}s (attempt {attempt}/{self.max_retries})..."
                )
                time.sleep(wait_time)

        if isinstance(last_exception, (httpx.ReadTimeout, httpx.WriteTimeout)):
            raise BodhanTimeoutError(f"Request to {url} timed out after {self.max_retries} attempts.")
        raise BodhanAPIError(500, f"Failed after {self.max_retries} attempts: {last_exception}")

    # -----------------------------------------------------------------------
    # Helper: Audio Duration Inspection
    # -----------------------------------------------------------------------
    def get_audio_duration(self, audio_path: Union[str, Path]) -> float:
        """Gets duration of an audio file in seconds via wave or header fallback."""
        path = Path(audio_path)
        try:
            with wave.open(str(path), "rb") as w:
                frames = w.getnframes()
                rate = w.getframerate()
                if rate > 0:
                    return float(frames) / float(rate)
        except Exception:
            pass

        # Fallback: estimate from file size assuming standard 16kHz 16-bit mono PCM (32 kB/s)
        size_bytes = path.stat().st_size
        return max(0.5, size_bytes / 32000.0)

    # -----------------------------------------------------------------------
    # Tool 1: Speech-to-Text (Indic-Transcribe)
    # -----------------------------------------------------------------------
    def transcribe(
        self,
        audio_path: Union[str, Path],
        language: Optional[str] = None,
        model: str = "indic-transcribe-core",
    ) -> Dict[str, Any]:
        """
        Transcribes audio using Bodhan Indic-Transcribe.
        Pricing: ₹0.10 per input audio minute.
        """
        path = Path(audio_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        norm_lang = normalize_lang_code(language) if language else None
        if norm_lang and not is_language_supported(norm_lang):
            raise BodhanUnsupportedLanguageError(norm_lang, "Transcription requires a supported Indic language or auto-detection.")

        duration_sec = self.get_audio_duration(path)
        file_hash = self._hash_file(path)
        cache_key = self._compute_cache_key("transcribe", {
            "file_hash": file_hash,
            "lang": norm_lang,
            "model": model,
        })

        cached_data = self._read_cache(cache_key)
        if cached_data:
            cached_data["cached"] = True
            saved_cost = (duration_sec / 60.0) * PRICING_INR["transcribe_per_minute"]
            self.total_saved_cost_inr += saved_cost
            return cached_data

        headers = self._get_headers(self.transcribe_key)
        form_data = {"model": model, "response_format": "verbose_json"}
        if norm_lang:
            form_data["language"] = norm_lang

        with open(path, "rb") as f:
            files = {"file": (path.name, f, "audio/wav")}
            resp = self._request_with_retry(
                method="POST",
                endpoint="/audio/transcriptions",
                headers=headers,
                data=form_data,
                files=files,
            )

        resp_json = resp.json()
        transcribed_text = resp_json.get("text", "").strip()
        detected_language = resp_json.get("language") or norm_lang or "unknown"
        cost_inr = round((duration_sec / 60.0) * PRICING_INR["transcribe_per_minute"], 5)

        result = {
            "text": transcribed_text,
            "detected_language": detected_language,
            "duration_seconds": round(duration_sec, 2),
            "cost_inr": cost_inr,
            "cached": False,
        }

        self._write_cache(cache_key, result)
        self.total_cost_inr += cost_inr
        self.history.append({"op": "transcribe", "cost_inr": cost_inr, "duration_sec": duration_sec})
        return result

    # -----------------------------------------------------------------------
    # Tool 2: Translation (Indic-Translate)
    # -----------------------------------------------------------------------
    def translate(
        self,
        text: str,
        target_language: str,
        source_language: str = "en",
        model: str = "indic-translate",
    ) -> Dict[str, Any]:
        """
        Translates text between Indic languages and English using Indic-Translate.
        Pricing: ₹0.20 per 10,000 output tokens.
        """
        text = text.strip()
        if not text:
            return {"translated_text": "", "source_language": source_language, "target_language": target_language, "cost_inr": 0.0, "cached": False}

        src_norm = normalize_lang_code(source_language)
        tgt_norm = normalize_lang_code(target_language)

        if not is_language_supported(src_norm):
            raise BodhanUnsupportedLanguageError(src_norm, f"Source language '{source_language}' is not supported.")
        if not is_language_supported(tgt_norm):
            raise BodhanUnsupportedLanguageError(tgt_norm, f"Target language '{target_language}' is not supported.")

        cache_key = self._compute_cache_key("translate", {
            "text": text,
            "src": src_norm,
            "tgt": tgt_norm,
            "model": model,
        })

        cached_data = self._read_cache(cache_key)
        if cached_data:
            cached_data["cached"] = True
            est_tokens = len(cached_data.get("translated_text", "").split()) * 1.33
            saved_cost = (est_tokens / 10000.0) * PRICING_INR["translate_per_10k_tokens"]
            self.total_saved_cost_inr += saved_cost
            return cached_data

        headers = self._get_headers(self.translate_key)
        payload = {
            "text": text,
            "source_lang": src_norm,
            "target_lang": tgt_norm,
        }

        resp = self._request_with_retry(
            method="POST",
            endpoint="/translations",
            headers=headers,
            json_payload=payload,
        )

        resp_json = resp.json()
        translated_text = resp_json.get("translated_text", "").strip()

        # Token counting: Indic scripts average ~1.33 tokens per word
        est_tokens = max(1, int(len(translated_text.split()) * 1.33))
        cost_inr = round((est_tokens / 10000.0) * PRICING_INR["translate_per_10k_tokens"], 6)

        result = {
            "translated_text": translated_text,
            "source_language": src_norm,
            "target_language": tgt_norm,
            "tokens": est_tokens,
            "cost_inr": cost_inr,
            "cached": False,
        }

        self._write_cache(cache_key, result)
        self.total_cost_inr += cost_inr
        self.history.append({"op": "translate", "cost_inr": cost_inr, "tokens": est_tokens})
        return result

    # -----------------------------------------------------------------------
    # Tool 3: Text-to-Speech (Indic-Speak)
    # -----------------------------------------------------------------------
    def speak(
        self,
        text: str,
        language: str = "hi",
        voice: Optional[str] = None,
        output_path: Optional[Union[str, Path]] = None,
        speed: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Synthesizes text into speech using Indic-Speak.
        Pricing: ₹6.00 per 10,000 input characters.
        """
        text = text.strip()
        if not text:
            raise ValueError("Input text for speech synthesis cannot be empty.")

        lang_norm = normalize_lang_code(language)
        if not is_language_supported(lang_norm):
            raise BodhanUnsupportedLanguageError(lang_norm, "Speech synthesis requires a supported Indic language.")

        # Default voice selection from fixed voice library
        selected_voice = voice
        if not selected_voice:
            voices = INDIC_SPEAK_SAMPLE_VOICES.get(lang_norm, ["Amit"])
            selected_voice = voices[0]

        char_count = len(text)
        cost_inr = round((char_count / 10000.0) * PRICING_INR["speak_per_10k_chars"], 5)

        cache_key = self._compute_cache_key("speak", {
            "text": text,
            "lang": lang_norm,
            "voice": selected_voice,
            "speed": speed,
        })

        cached_audio_path = self.cache_dir / f"speech_{cache_key}.wav"
        cached_meta = self._read_cache(cache_key)

        # Target output file path
        if output_path:
            out_file = Path(output_path).resolve()
            out_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_file = cached_audio_path

        if cached_meta and cached_audio_path.exists():
            if str(out_file) != str(cached_audio_path):
                shutil.copyfile(cached_audio_path, out_file)
            cached_meta["cached"] = True
            cached_meta["audio_path"] = str(out_file)
            self.total_saved_cost_inr += cost_inr
            return cached_meta

        headers = self._get_headers(self.speak_key)
        payload = {
            "model": "indic-speak",
            "input": text,
            "voice": selected_voice,
            "speed": speed,
            "response_format": "wav",
        }

        resp = self._request_with_retry(
            method="POST",
            endpoint="/audio/speech",
            headers=headers,
            json_payload=payload,
        )

        # Write binary WAV content
        with open(cached_audio_path, "wb") as f:
            f.write(resp.content)

        if str(out_file) != str(cached_audio_path):
            shutil.copyfile(cached_audio_path, out_file)

        duration_sec = self.get_audio_duration(out_file)

        result = {
            "audio_path": str(out_file),
            "duration_seconds": round(duration_sec, 2),
            "character_count": char_count,
            "voice": selected_voice,
            "cost_inr": cost_inr,
            "cached": False,
        }

        self._write_cache(cache_key, result)
        self.total_cost_inr += cost_inr
        self.history.append({"op": "speak", "cost_inr": cost_inr, "chars": char_count})
        return result

    # -----------------------------------------------------------------------
    # Tool 4: OCR (Indic-OCR)
    # -----------------------------------------------------------------------
    def ocr(self, image_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Extracts text from images or scanned pages using Indic-OCR.
        Pricing: ₹0.20 per image.
        """
        path = Path(image_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")

        file_hash = self._hash_file(path)
        cache_key = self._compute_cache_key("ocr", {"file_hash": file_hash})

        cost_inr = PRICING_INR["ocr_per_image"]
        cached_data = self._read_cache(cache_key)
        if cached_data:
            cached_data["cached"] = True
            self.total_saved_cost_inr += cost_inr
            return cached_data

        headers = self._get_headers(self.ocr_key)
        suffix = path.suffix.lower()
        mime = "image/png" if suffix in (".png",) else "image/jpeg"

        with open(path, "rb") as f:
            files = {"file": (path.name, f, mime)}
            resp = self._request_with_retry(
                method="POST",
                endpoint="/ocr",
                headers=headers,
                files=files,
            )

        resp_json = resp.json()

        # Parse text from OCR format (structured blocks or plain text)
        extracted_text = ""
        detected_script = "Indic"
        if isinstance(resp_json, dict):
            extracted_text = resp_json.get("text") or resp_json.get("markdown") or ""
            if not extracted_text and "blocks" in resp_json:
                extracted_text = "\n".join([b.get("text", "") for b in resp_json["blocks"] if "text" in b])
            detected_script = resp_json.get("script", "Indic")
        elif isinstance(resp_json, str):
            extracted_text = resp_json

        result = {
            "extracted_text": extracted_text.strip(),
            "detected_script": detected_script,
            "image_path": str(path),
            "cost_inr": cost_inr,
            "cached": False,
        }

        self._write_cache(cache_key, result)
        self.total_cost_inr += cost_inr
        self.history.append({"op": "ocr", "cost_inr": cost_inr})
        return result

    # -----------------------------------------------------------------------
    # Budget & Cost Helpers
    # -----------------------------------------------------------------------
    def get_total_cost(self) -> float:
        """Returns cumulative ₹ cost incurred in this session."""
        return round(self.total_cost_inr, 5)

    def get_saved_cost(self) -> float:
        """Returns cumulative ₹ cost saved by cache hits."""
        return round(self.total_saved_cost_inr, 5)

    def reset_cost(self):
        """Resets the session cost counter."""
        self.total_cost_inr = 0.0
        self.total_saved_cost_inr = 0.0
        self.history = []
