"""
Bodhan API Client — shared HTTP client for all four Indic models.

Features:
- Retry with exponential backoff (via tenacity)
- Concurrency limiter (asyncio.Semaphore)
- Per-call cost tracking
- Hash-based response caching
- Per-call timeout enforcement
- Structured error responses
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost constants (per the assignment spec)
# ---------------------------------------------------------------------------
COST_TRANSCRIBE_PER_MINUTE = 0.10       # ₹0.10 per input audio minute
COST_TRANSLATE_PER_10K_TOKENS = 0.20    # ₹0.20 per 10,000 output tokens
COST_SPEAK_PER_10K_CHARS = 6.00         # ₹6.00 per 10,000 input characters
COST_OCR_PER_IMAGE = 0.20               # ₹0.20 per image

# Supported languages for Indic-Translate
SUPPORTED_LANGUAGE_CODES = frozenset({
    "en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa",
    "or", "as", "ur", "ne", "sa", "sd", "sd-Deva", "sd-Arab",
    "kok", "doi", "mai", "brx", "sat", "ks", "ks-Deva",
    "mni", "mni-Beng",
})

# Default voice per language for Indic-Speak
DEFAULT_VOICES: dict[str, str] = {
    "hi": "Kalpana",
    "bn": "Ishita",
    "ta": "Kavya",
    "te": "Kavya",
    "mr": "Kalpana",
    "gu": "Kavya",
    "kn": "Kavya",
    "ml": "Kavya",
    "pa": "Kavya",
    "en": "Kavya",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class BodhanAPIError(Exception):
    """Raised when a Bodhan API call fails."""

    def __init__(self, message: str, status_code: int | None = None, model: str = ""):
        self.status_code = status_code
        self.model = model
        super().__init__(message)


class BudgetExceededError(Exception):
    """Raised when the cost or step budget is exhausted."""
    pass


class UnsupportedLanguageError(BodhanAPIError):
    """Raised when a requested language is not supported."""
    pass


# ---------------------------------------------------------------------------
# Cost Tracker
# ---------------------------------------------------------------------------
@dataclass
class CostTracker:
    """Tracks cumulative cost across all API calls."""

    max_cost_inr: float = 20.0
    total_cost_inr: float = 0.0
    call_log: list[dict[str, Any]] = field(default_factory=list)

    def add(self, model: str, cost_inr: float, details: str = "") -> None:
        """Record a cost entry. Raises BudgetExceededError if budget exceeded."""
        self.total_cost_inr += cost_inr
        entry = {
            "model": model,
            "cost_inr": round(cost_inr, 4),
            "cumulative_inr": round(self.total_cost_inr, 4),
            "details": details,
            "timestamp": time.time(),
        }
        self.call_log.append(entry)
        logger.info(f"Cost: ₹{cost_inr:.4f} ({model}) — Total: ₹{self.total_cost_inr:.4f}")

        if self.total_cost_inr > self.max_cost_inr:
            raise BudgetExceededError(
                f"Budget exceeded: ₹{self.total_cost_inr:.2f} > ₹{self.max_cost_inr:.2f}"
            )

    def estimate_cost(self, model: str, **kwargs) -> float:
        """Estimate cost for a planned call (pre-check)."""
        if model == "indic-transcribe":
            minutes = kwargs.get("duration_seconds", 0) / 60.0
            return minutes * COST_TRANSCRIBE_PER_MINUTE
        elif model == "indic-translate":
            tokens = kwargs.get("estimated_output_tokens", 0)
            return (tokens / 10_000) * COST_TRANSLATE_PER_10K_TOKENS
        elif model == "indic-speak":
            chars = kwargs.get("input_characters", 0)
            return (chars / 10_000) * COST_SPEAK_PER_10K_CHARS
        elif model == "indic-ocr":
            images = kwargs.get("num_images", 1)
            return images * COST_OCR_PER_IMAGE
        return 0.0

    def would_exceed(self, model: str, **kwargs) -> bool:
        """Check if a planned call would exceed the budget."""
        estimated = self.estimate_cost(model, **kwargs)
        return (self.total_cost_inr + estimated) > self.max_cost_inr

    def summary(self) -> dict[str, Any]:
        """Return a cost summary."""
        return {
            "total_cost_inr": round(self.total_cost_inr, 4),
            "max_cost_inr": self.max_cost_inr,
            "remaining_inr": round(self.max_cost_inr - self.total_cost_inr, 4),
            "num_calls": len(self.call_log),
            "call_log": self.call_log,
        }


# ---------------------------------------------------------------------------
# Response Cache
# ---------------------------------------------------------------------------
class ResponseCache:
    """Hash-based cache to avoid duplicate API calls."""

    def __init__(self, cache_dir: Path | None = None):
        self._cache_dir = cache_dir or Path(".bodhan_cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, Any] = {}

    def _make_key(self, model: str, params: dict[str, Any], file_path: str | None = None) -> str:
        """Create a unique cache key from model + params + optional file hash."""
        key_data = {"model": model, "params": params}
        if file_path and Path(file_path).exists():
            with open(file_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            key_data["file_hash"] = file_hash
        raw = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, model: str, params: dict[str, Any], file_path: str | None = None) -> Any | None:
        """Look up a cached response. Returns None on miss."""
        key = self._make_key(model, params, file_path)
        # Check memory first
        if key in self._memory:
            logger.debug(f"Cache HIT (memory): {key[:12]}...")
            return self._memory[key]
        # Check disk
        disk_path = self._cache_dir / f"{key}.json"
        if disk_path.exists():
            try:
                data = json.loads(disk_path.read_text(encoding="utf-8"))
                self._memory[key] = data
                logger.debug(f"Cache HIT (disk): {key[:12]}...")
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def put(
        self, model: str, params: dict[str, Any], response: Any, file_path: str | None = None
    ) -> None:
        """Store a response in cache (memory + disk)."""
        key = self._make_key(model, params, file_path)
        self._memory[key] = response
        disk_path = self._cache_dir / f"{key}.json"
        try:
            disk_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            logger.debug(f"Cache PUT: {key[:12]}...")
        except (TypeError, OSError) as e:
            logger.warning(f"Failed to write cache to disk: {e}")


# ---------------------------------------------------------------------------
# Bodhan API Client
# ---------------------------------------------------------------------------
class BodhanClient:
    """
    Async HTTP client for the Bodhan hosted API.

    Usage:
        client = BodhanClient(cost_tracker=tracker)
        text = await client.transcribe("audio.wav")
        translated = await client.translate("Hello", source="en", target="hi")
        await client.speak("नमस्ते", lang="hi", voice="Kalpana", output_path="out.wav")
        markdown = await client.ocr("page.png")
    """

    def __init__(
        self,
        base_url: str | None = None,
        cost_tracker: CostTracker | None = None,
        cache: ResponseCache | None = None,
        max_concurrent: int = 3,
        timeout_seconds: float = 120.0,
    ):
        self._base_url = (base_url or os.getenv("BODHAN_API_BASE_URL", "https://api.bodhan.ai/v1")).rstrip("/")
        self._keys = {
            "indic-transcribe": os.getenv("BODHAN_TRANSCRIBE_KEY", ""),
            "indic-translate": os.getenv("BODHAN_TRANSLATE_KEY", ""),
            "indic-speak": os.getenv("BODHAN_SPEAK_KEY", ""),
            "indic-ocr": os.getenv("BODHAN_OCR_KEY", ""),
        }
        self.cost_tracker = cost_tracker or CostTracker()
        self.cache = cache or ResponseCache()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout = timeout_seconds
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    def _get_key(self, model: str) -> str:
        key = self._keys.get(model, "")
        if not key:
            raise BodhanAPIError(f"No API key configured for {model}", model=model)
        return key

    # -------------------------------------------------------------------
    # Transcribe (Speech-to-Text)
    # -------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        duration_seconds: float | None = None,
    ) -> dict[str, Any]:
        """
        Transcribe an audio file using Indic-Transcribe.

        Args:
            audio_path: Path to audio file (WAV, MP3, FLAC, OGG).
            language: Optional ISO language code hint (e.g., 'hi', 'ta').
            duration_seconds: Audio duration for cost tracking. If None, cost is estimated as 0.

        Returns:
            dict with 'text' (transcribed text) and 'language' (detected).
        """
        path = Path(audio_path)
        if not path.exists():
            raise BodhanAPIError(f"Audio file not found: {audio_path}", model="indic-transcribe")

        # Cache check
        params = {"language": language}
        cached = self.cache.get("indic-transcribe", params, audio_path)
        if cached is not None:
            return cached

        # Budget pre-check
        dur = duration_seconds or 0
        if self.cost_tracker.would_exceed("indic-transcribe", duration_seconds=dur):
            raise BudgetExceededError(
                f"Transcription of {dur:.0f}s would exceed budget "
                f"(remaining: ₹{self.cost_tracker.max_cost_inr - self.cost_tracker.total_cost_inr:.2f})"
            )

        async with self._semaphore:
            http = await self._get_http()
            key = self._get_key("indic-transcribe")

            # Build multipart form
            files_data = {"file": (path.name, open(path, "rb"), "application/octet-stream")}
            data = {"model": "indic-transcribe"}
            if language:
                data["language"] = language

            start_time = time.time()
            resp = await http.post(
                f"{self._base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files=files_data,
                data=data,
            )
            latency = time.time() - start_time

        if resp.status_code != 200:
            raise BodhanAPIError(
                f"Transcribe failed: {resp.status_code} — {resp.text}",
                status_code=resp.status_code,
                model="indic-transcribe",
            )

        result = resp.json()
        output = {
            "text": result.get("text", ""),
            "language": language or "auto",
            "latency_seconds": round(latency, 2),
        }

        # Track cost
        cost = (dur / 60.0) * COST_TRANSCRIBE_PER_MINUTE if dur > 0 else COST_TRANSCRIBE_PER_MINUTE * 0.5
        self.cost_tracker.add("indic-transcribe", cost, f"file={path.name}, dur={dur:.0f}s")

        # Cache the result
        self.cache.put("indic-transcribe", params, output, audio_path)
        return output

    # -------------------------------------------------------------------
    # Translate
    # -------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> dict[str, Any]:
        """
        Translate text using Indic-Translate.

        Args:
            text: Text to translate.
            source_language: ISO code of source language.
            target_language: ISO code of target language.

        Returns:
            dict with 'translated_text', 'source_language', 'target_language'.
        """
        # Validate languages
        for lang, label in [(source_language, "source"), (target_language, "target")]:
            if lang not in SUPPORTED_LANGUAGE_CODES:
                raise UnsupportedLanguageError(
                    f"Unsupported {label} language: '{lang}'. "
                    f"Supported: {sorted(SUPPORTED_LANGUAGE_CODES)}",
                    model="indic-translate",
                )

        if source_language == target_language:
            return {
                "translated_text": text,
                "source_language": source_language,
                "target_language": target_language,
                "note": "Source and target are the same; returning original text.",
            }

        # Cache check
        params = {"source": source_language, "target": target_language, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
        cached = self.cache.get("indic-translate", params)
        if cached is not None:
            return cached

        # Budget pre-check (rough estimate: output ≈ input tokens)
        est_tokens = len(text.split()) * 1.5
        if self.cost_tracker.would_exceed("indic-translate", estimated_output_tokens=est_tokens):
            raise BudgetExceededError("Translation would exceed budget")

        async with self._semaphore:
            http = await self._get_http()
            key = self._get_key("indic-translate")

            payload = {
                "model": "indic-translate",
                "messages": [{"role": "user", "content": text}],
                "source_language_code": source_language,
                "target_language_code": target_language,
            }

            start_time = time.time()
            resp = await http.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            latency = time.time() - start_time

        if resp.status_code != 200:
            raise BodhanAPIError(
                f"Translate failed: {resp.status_code} — {resp.text}",
                status_code=resp.status_code,
                model="indic-translate",
            )

        data = resp.json()
        translated_text = data["choices"][0]["message"]["content"]
        output_tokens = data.get("usage", {}).get("completion_tokens", len(translated_text.split()))

        output = {
            "translated_text": translated_text,
            "source_language": source_language,
            "target_language": target_language,
            "latency_seconds": round(latency, 2),
        }

        # Track cost
        cost = (output_tokens / 10_000) * COST_TRANSLATE_PER_10K_TOKENS
        self.cost_tracker.add("indic-translate", cost, f"{source_language}→{target_language}, {output_tokens} tokens")

        self.cache.put("indic-translate", params, output)
        return output

    # -------------------------------------------------------------------
    # Speak (Text-to-Speech)
    # -------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def speak(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        output_path: str = "output.wav",
        style: str = "default",
    ) -> dict[str, Any]:
        """
        Synthesize speech using Indic-Speak.

        Args:
            text: Text to speak (keep short — model has limits).
            language: ISO code of the language.
            voice: Voice name (e.g., 'Kavya', 'Kalpana'). Auto-selected if None.
            output_path: Where to save the audio file.
            style: Speaking style (e.g., 'news', 'conversational', 'default').

        Returns:
            dict with 'output_path', 'voice', 'language', 'characters', 'cost_inr'.
        """
        if not text.strip():
            raise BodhanAPIError("Cannot synthesize empty text", model="indic-speak")

        voice = voice or DEFAULT_VOICES.get(language, "Kavya")
        char_count = len(text)

        # Budget pre-check (TTS is expensive!)
        if self.cost_tracker.would_exceed("indic-speak", input_characters=char_count):
            raise BudgetExceededError(
                f"Speech synthesis of {char_count} chars would exceed budget"
            )

        async with self._semaphore:
            http = await self._get_http()
            key = self._get_key("indic-speak")

            instructions = json.dumps({"lang": language, "style": style})
            payload = {
                "model": "indic-speak",
                "input": text,
                "voice": voice,
                "instructions": instructions,
            }

            start_time = time.time()
            resp = await http.post(
                f"{self._base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            latency = time.time() - start_time

        if resp.status_code != 200:
            raise BodhanAPIError(
                f"Speak failed: {resp.status_code} — {resp.text}",
                status_code=resp.status_code,
                model="indic-speak",
            )

        # Write audio to file
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)

        cost = (char_count / 10_000) * COST_SPEAK_PER_10K_CHARS
        self.cost_tracker.add("indic-speak", cost, f"voice={voice}, chars={char_count}")

        return {
            "output_path": str(out_path),
            "voice": voice,
            "language": language,
            "characters": char_count,
            "cost_inr": round(cost, 4),
            "latency_seconds": round(latency, 2),
            "note": "AI-generated audio. Not a real human voice.",
        }

    # -------------------------------------------------------------------
    # OCR (Document Digitization)
    # -------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def ocr(
        self,
        image_path: str,
        table_format: str = "markdown",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """
        Extract text from an image using Indic-OCR.

        Args:
            image_path: Path to a PNG or JPEG image (one page only).
            table_format: 'markdown' or 'html' for table rendering.
            max_tokens: Maximum tokens per block.

        Returns:
            dict with 'text' (Markdown), 'blocks' (layout info), 'confidence'.
        """
        path = Path(image_path)
        if not path.exists():
            raise BodhanAPIError(f"Image file not found: {image_path}", model="indic-ocr")

        # Cache check
        params = {"table_format": table_format, "max_tokens": max_tokens}
        cached = self.cache.get("indic-ocr", params, image_path)
        if cached is not None:
            return cached

        # Budget pre-check
        if self.cost_tracker.would_exceed("indic-ocr", num_images=1):
            raise BudgetExceededError("OCR would exceed budget")

        # Read and encode image as base64
        image_bytes = path.read_bytes()
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:{mime};base64,{b64}"

        async with self._semaphore:
            http = await self._get_http()
            key = self._get_key("indic-ocr")

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
                "table_format": table_format,
                "max_tokens": max_tokens,
            }

            start_time = time.time()
            resp = await http.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            latency = time.time() - start_time

        if resp.status_code != 200:
            raise BodhanAPIError(
                f"OCR failed: {resp.status_code} — {resp.text}",
                status_code=resp.status_code,
                model="indic-ocr",
            )

        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        blocks = data.get("blocks", [])

        # Calculate average confidence from blocks
        confidences = [b.get("conf", 0) for b in blocks if "conf" in b]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        output = {
            "text": text,
            "blocks": blocks,
            "confidence": round(avg_conf, 3),
            "latency_seconds": round(latency, 2),
        }

        cost = COST_OCR_PER_IMAGE
        self.cost_tracker.add("indic-ocr", cost, f"file={path.name}")

        self.cache.put("indic-ocr", params, output, image_path)
        return output
