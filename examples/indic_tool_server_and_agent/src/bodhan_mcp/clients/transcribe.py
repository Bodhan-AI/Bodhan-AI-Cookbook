"""Async client for Indic-Transcribe (Speech-to-Text)."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx

from bodhan_mcp.config import settings
from bodhan_mcp.core.cache import cache_get, cache_set, file_content_hash
from bodhan_mcp.core.chunking import chunk_audio, get_audio_duration_seconds
from bodhan_mcp.core.cost import calculate_stt_cost
from bodhan_mcp.core.errors import (
    BodhanError,
    INVALID_INPUT,
    FILE_NOT_FOUND,
    RATE_LIMITED,
    TIMEOUT,
    UPSTREAM_ERROR,
    UNSUPPORTED_LANGUAGE,
)
from bodhan_mcp.core.retry import bodhan_retry

logger = logging.getLogger(__name__)


@dataclass
class TranscribeResult:
    text: str
    timestamps: List[Dict[str, Any]] = field(default_factory=list)
    duration: float = 0.0
    cost: float = 0.0
    detected_language: Optional[str] = None
    cached: bool = False


class TranscribeClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.transcribe_key
        self.base_url = (base_url or settings.bodhan_base_url).rstrip("/")
        self.endpoint = f"{self.base_url}/audio/transcriptions"

    @bodhan_retry
    async def transcribe_chunk(
        self,
        file_path: str,
        language: Optional[str] = None,
        vad: bool = True,
    ) -> Dict[str, Any]:
        """Transcribe an audio chunk (<= 30s) using Indic-Transcribe.
        
        Returns dict containing at least {"text": str}.
        """
        if not os.path.exists(file_path):
            raise BodhanError(FILE_NOT_FOUND, f"Audio file not found: {file_path}")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        data: Dict[str, Any] = {
            "model": "indic-transcribe",
            "vad": "true" if vad else "false",
        }
        if language:
            data["language"] = language

        p = Path(file_path)
        temp_wav: Optional[str] = None
        actual_upload_path = file_path
        mime = "audio/wav"

        if p.suffix.lower() in (".mp3",):
            mime = "audio/mpeg"
        elif p.suffix.lower() in (".ogg",):
            mime = "audio/ogg"
        elif p.suffix.lower() not in (".wav", ".flac"):
            # Bodhan API requires WAV, FLAC, OGG, or MP3. Convert other formats (m4a, aac, etc.) to WAV.
            try:
                import tempfile
                from pydub import AudioSegment
                seg = AudioSegment.from_file(file_path)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                    temp_wav = tf.name
                seg.export(temp_wav, format="wav")
                actual_upload_path = temp_wav
                p = Path(temp_wav)
                mime = "audio/wav"
                logger.info("Converted %s to temporary WAV %s for Indic-Transcribe", file_path, temp_wav)
            except Exception as conv_err:
                logger.warning("Could not convert %s to WAV: %s", file_path, conv_err)

        logger.info("Calling Indic-Transcribe on %s (lang=%s, vad=%s)", p.name, language, vad)

        try:
            with open(actual_upload_path, "rb") as f:
                files = {"file": (p.name, f, mime)}
                async with httpx.AsyncClient(timeout=settings.api_timeout_seconds) as client:
                    resp = await client.post(
                        self.endpoint,
                        headers=headers,
                        data=data,
                        files=files,
                    )

            if resp.status_code == 200:
                result = resp.json()
                logger.info("Transcribe success for %s", p.name)
                return result
            elif resp.status_code == 429:
                raise BodhanError(RATE_LIMITED, "Transcribe API rate limit exceeded", retryable=True)
            elif resp.status_code in (408, 504):
                raise BodhanError(TIMEOUT, f"Transcribe API timed out: {resp.text}", retryable=True)
            elif resp.status_code >= 500:
                raise BodhanError(UPSTREAM_ERROR, f"Transcribe API server error ({resp.status_code}): {resp.text}", retryable=True)
            else:
                raise BodhanError(INVALID_INPUT, f"Transcribe API error ({resp.status_code}): {resp.text}", retryable=False)

        except httpx.TimeoutException as exc:
            raise BodhanError(TIMEOUT, f"Transcribe request timed out: {exc}", retryable=True)
        except httpx.RequestError as exc:
            raise BodhanError(UPSTREAM_ERROR, f"Transcribe network request error: {exc}", retryable=True)
        finally:
            if temp_wav and os.path.exists(temp_wav):
                try:
                    os.remove(temp_wav)
                except Exception:
                    pass

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        out_dir: Optional[str] = None,
        vad: bool = True,
    ) -> TranscribeResult:
        """High-level transcribe method that handles chunking, caching, and cost."""
        p = Path(audio_path)
        if not p.exists():
            raise BodhanError(FILE_NOT_FOUND, f"Audio file not found: {audio_path}")

        duration = get_audio_duration_seconds(audio_path)
        file_hash = file_content_hash(audio_path)
        cache_params = {"lang": language, "vad": vad, "duration": duration}
        cached = cache_get("indic-transcribe", cache_params, file_hash)
        if cached:
            return TranscribeResult(
                text=cached.get("full_text", cached.get("text", "")),
                timestamps=cached.get("segments", cached.get("timestamps", [])),
                duration=duration,
                cost=0.0,
                detected_language=cached.get("detected_language"),
                cached=True,
            )

        chunks = chunk_audio(audio_path, chunk_seconds=settings.stt_max_chunk_seconds, output_dir=out_dir)
        text_parts = []
        timestamps = []
        detected_lang = language

        for chunk_file, start_sec, end_sec in chunks:
            res = await self.transcribe_chunk(chunk_file, language=language, vad=vad)
            txt = res.get("text", "").strip()
            if "language" in res and not detected_lang:
                detected_lang = res["language"]
            if txt:
                text_parts.append(txt)
                timestamps.append({
                    "start_seconds": round(start_sec, 2),
                    "end_seconds": round(end_sec, 2),
                    "text": txt,
                })

        full_text = " ".join(text_parts)
        cost = calculate_stt_cost(duration)

        result_data = {
            "text": full_text,
            "full_text": full_text,
            "timestamps": timestamps,
            "segments": timestamps,
            "duration": duration,
            "detected_language": detected_lang,
        }
        cache_set("indic-transcribe", cache_params, file_hash, result_data)

        return TranscribeResult(
            text=full_text,
            timestamps=timestamps,
            duration=duration,
            cost=cost,
            detected_language=detected_lang,
            cached=False,
        )
