"""
HTTP client for Bodhan AI Indic Models with retries, exponential backoff,
and testing/mock fallback mode.
"""

import os
import time
import json
import logging
from typing import Optional, Dict, Any, List
import httpx

from .pricing import (
    calculate_transcribe_cost,
    calculate_translate_cost,
    calculate_speak_cost,
    calculate_ocr_cost,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.getenv("BODHAN_API_BASE_URL", "https://api.bodhan.ai/v1")


class BodhanAPIError(Exception):
    """Custom exception for Bodhan API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class BodhanClient:
    """Client for Bodhan Indic AI services."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.transcribe_key = os.getenv("BODHAN_TRANSCRIBE_KEY", "")
        self.translate_key = os.getenv("BODHAN_TRANSLATE_KEY", "")
        self.speak_key = os.getenv("BODHAN_SPEAK_KEY", "")
        self.ocr_key = os.getenv("BODHAN_OCR_KEY", "")

    def _is_mock_mode(self, key: str) -> bool:
        """Check if client should run in mock/test mode due to absent/placeholder key."""
        if not key or key.startswith("your_") or key == "test_key" or "placeholder" in key:
            return True
        return False

    def transcribe(
        self,
        audio_file_path: str,
        duration_seconds: float,
        language: Optional[str] = None,
        model_variant: str = "core"
    ) -> Dict[str, Any]:
        """
        Call Indic-Transcribe to transcribe an audio chunk (<= 30s).
        Returns dict with 'text', 'language', 'segments', 'cost_inr', 'latency_ms'.
        """
        start_time = time.time()
        cost = calculate_transcribe_cost(duration_seconds)
        model_name = f"indic-transcribe-{model_variant}"

        if self._is_mock_mode(self.transcribe_key):
            # Deterministic simulation for test/mock runs
            time.sleep(0.05)
            detected_lang = language or "hi"
            simulated_text = f"यह {os.path.basename(audio_file_path)} का परीक्षण प्रतिलेखन है।" if detected_lang == "hi" else f"This is a transcribed sample from {os.path.basename(audio_file_path)}."
            latency = (time.time() - start_time) * 1000
            return {
                "text": simulated_text,
                "language": detected_lang,
                "segments": [
                    {"start_seconds": 0.0, "end_seconds": duration_seconds, "text": simulated_text}
                ],
                "cost_inr": cost,
                "latency_ms": latency,
                "model_name": model_name,
            }

        headers = {
            "Authorization": f"Bearer {self.transcribe_key}",
        }
        with open(audio_file_path, "rb") as f:
            files = {"file": (os.path.basename(audio_file_path), f, "audio/wav")}
            data = {"model": model_name}
            if language:
                data["language"] = language

            url = f"{self.base_url}/audio/transcriptions"
            resp = self._post_with_retry(url, headers=headers, files=files, data=data)
            latency = (time.time() - start_time) * 1000
            resp_data = resp.json()
            return {
                "text": resp_data.get("text", ""),
                "language": resp_data.get("language", language or "unknown"),
                "segments": resp_data.get("segments", []),
                "cost_inr": cost,
                "latency_ms": latency,
                "model_name": model_name,
            }

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> Dict[str, Any]:
        """
        Call Indic-Translate to translate text between languages.
        Returns dict with 'translated_text', 'token_count', 'cost_inr', 'latency_ms'.
        """
        start_time = time.time()
        # Rough token count: approx 1 token per 4 characters
        token_count = max(1, len(text) // 4)
        cost = calculate_translate_cost(token_count)
        model_name = "indic-translate"

        # Check unsupported language
        unsupported = {"fr", "french", "de", "german", "es", "spanish", "zh", "chinese"}
        if target_lang.lower() in unsupported or source_lang.lower() in unsupported:
            raise BodhanAPIError(
                f"Unsupported language: '{target_lang}' is not an Indic language or supported pair. Indic-Translate supports Indian languages and English.",
                status_code=400,
                details={"unsupported_language": target_lang}
            )

        if self._is_mock_mode(self.translate_key):
            time.sleep(0.05)
            # Simulated translations for mock/testing
            translations_map = {
                "hi": "नमस्ते, यह अनुवादित पाठ है।",
                "ta": "வணக்கம், இது மொழிபெயர்க்கப்பட்ட உரை.",
                "bn": "নমস্কার, এটি অনুবাদিত পাঠ্য।",
                "mr": "नमस्कार, हा भाषांतरित मजकूर आहे.",
                "te": "నమస్కారం, ఇది అనువదించబడిన వచనం.",
                "en": f"Translated to English: {text}",
            }
            sample_tr = translations_map.get(target_lang, f"[{target_lang.upper()} translation of: {text[:60]}...]")
            latency = (time.time() - start_time) * 1000
            return {
                "translated_text": sample_tr,
                "token_count": token_count,
                "cost_inr": cost,
                "latency_ms": latency,
                "model_name": model_name,
            }

        headers = {
            "Authorization": f"Bearer {self.translate_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": f"Translate the following text from {source_lang} to {target_lang}:\n\n{text}"
                }
            ],
            "temperature": 0.1,
        }
        url = f"{self.base_url}/chat/completions"
        resp = self._post_with_retry(url, headers=headers, json_payload=payload)
        latency = (time.time() - start_time) * 1000
        res_json = resp.json()
        translated_text = res_json["choices"][0]["message"]["content"]
        tokens = res_json.get("usage", {}).get("completion_tokens", token_count)
        return {
            "translated_text": translated_text,
            "token_count": tokens,
            "cost_inr": calculate_translate_cost(tokens),
            "latency_ms": latency,
            "model_name": model_name,
        }

    def speak(
        self,
        text: str,
        language: str,
        output_path: str,
        gender: str = "female"
    ) -> Dict[str, Any]:
        """
        Call Indic-Speak to synthesize speech from Indian-language text.
        Returns dict with 'audio_path', 'duration_seconds', 'character_count', 'cost_inr', 'latency_ms'.
        """
        start_time = time.time()
        char_count = len(text)
        cost = calculate_speak_cost(char_count)
        model_name = "indic-speak"

        unsupported = {"fr", "french", "de", "german", "es", "spanish", "zh", "chinese"}
        if language.lower() in unsupported:
            raise BodhanAPIError(
                f"Unsupported language for Indic-Speak: '{language}'. Indic-Speak only supports Indic languages.",
                status_code=400,
                details={"unsupported_language": language}
            )

        if self._is_mock_mode(self.speak_key):
            time.sleep(0.05)
            # Create a valid minimal WAV file for testing if audio doesn't exist
            import wave
            import struct
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            # Estimate duration: approx 15 chars per second for natural Indic speech
            duration = max(1.0, round(char_count / 15.0, 2))
            sample_rate = 16000
            total_frames = int(sample_rate * min(duration, 5.0))  # write max 5s of silence for test

            with wave.open(output_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(b"".join(struct.pack("<h", 0) for _ in range(total_frames)))

            latency = (time.time() - start_time) * 1000
            return {
                "audio_path": output_path,
                "duration_seconds": duration,
                "character_count": char_count,
                "cost_inr": cost,
                "latency_ms": latency,
                "model_name": model_name,
            }

        headers = {
            "Authorization": f"Bearer {self.speak_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "input": text,
            "voice": f"{language}_{gender}",
            "response_format": "mp3",
        }
        url = f"{self.base_url}/audio/speech"
        resp = self._post_with_retry(url, headers=headers, json_payload=payload)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(resp.content)

        latency = (time.time() - start_time) * 1000
        # Estimate duration roughly based on character count (15 chars/sec)
        est_duration = max(1.0, round(char_count / 15.0, 2))
        return {
            "audio_path": output_path,
            "duration_seconds": est_duration,
            "character_count": char_count,
            "cost_inr": cost,
            "latency_ms": latency,
            "model_name": model_name,
        }

    def ocr(
        self,
        image_path: str,
        extract_tables: bool = True,
        extract_equations: bool = True
    ) -> Dict[str, Any]:
        """
        Call Indic-OCR to extract structured Markdown text from an image.
        Returns dict with 'text', 'has_tables', 'has_equations', 'cost_inr', 'latency_ms'.
        """
        start_time = time.time()
        cost = calculate_ocr_cost(1)
        model_name = "indic-ocr"

        if self._is_mock_mode(self.ocr_key):
            time.sleep(0.05)
            filename = os.path.basename(image_path)
            simulated_text = (
                f"# Extracted Content from {filename}\n\n"
                f"This is the structured OCR content extracted from {filename}.\n\n"
                f"| Column 1 | Column 2 |\n|---|---|\n| Data A | Data B |\n\n"
                f"Equation: $x = \\frac{{-b \\pm \\sqrt{{b^2 - 4ac}}}}{{2a}}$\n"
            )
            latency = (time.time() - start_time) * 1000
            return {
                "text": simulated_text,
                "has_tables": True,
                "has_equations": True,
                "cost_inr": cost,
                "latency_ms": latency,
                "model_name": model_name,
            }

        headers = {
            "Authorization": f"Bearer {self.ocr_key}",
        }
        with open(image_path, "rb") as f:
            files = {"file": (os.path.basename(image_path), f, "image/png")}
            data = {
                "model": model_name,
                "extract_tables": str(extract_tables).lower(),
                "extract_equations": str(extract_equations).lower(),
            }
            url = f"{self.base_url}/ocr/extract"
            resp = self._post_with_retry(url, headers=headers, files=files, data=data)
            latency = (time.time() - start_time) * 1000
            res_json = resp.json()
            return {
                "text": res_json.get("text", ""),
                "has_tables": res_json.get("has_tables", False),
                "has_equations": res_json.get("has_equations", False),
                "cost_inr": cost,
                "latency_ms": latency,
                "model_name": model_name,
            }

    def _post_with_retry(
        self,
        url: str,
        headers: Dict[str, str],
        files: Optional[Dict] = None,
        data: Optional[Dict] = None,
        json_payload: Optional[Dict] = None,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
    ) -> httpx.Response:
        """Execute HTTP POST with exponential backoff on transient errors."""
        last_err = None
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=60.0) as client:
                    if files:
                        resp = client.post(url, headers=headers, files=files, data=data)
                    else:
                        resp = client.post(url, headers=headers, json=json_payload)

                    if resp.status_code == 200:
                        return resp

                    if resp.status_code in (429, 500, 502, 503, 504):
                        # Transient error, retry
                        sleep_time = backoff_factor ** attempt
                        time.sleep(sleep_time)
                        continue

                    # Client error
                    raise BodhanAPIError(
                        f"Bodhan API error ({resp.status_code}): {resp.text}",
                        status_code=resp.status_code,
                        details={"body": resp.text}
                    )
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_err = e
                sleep_time = backoff_factor ** attempt
                time.sleep(sleep_time)

        raise BodhanAPIError(f"Bodhan API failed after {max_retries} attempts: {last_err}")
