"""Async client for Indic-Speak (Text-to-Speech)."""
from __future__ import annotations

import io
import os
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx

from bodhan_mcp.config import settings
from bodhan_mcp.core.cache import cache_get, cache_set
from bodhan_mcp.core.chunking import chunk_text_for_tts, get_audio_duration_seconds
from bodhan_mcp.core.cost import calculate_tts_cost
from bodhan_mcp.core.errors import (
    BodhanError,
    INVALID_INPUT,
    RATE_LIMITED,
    TIMEOUT,
    UPSTREAM_ERROR,
)
from bodhan_mcp.core.file_store import default_file_store
from bodhan_mcp.core.retry import bodhan_retry

logger = logging.getLogger(__name__)


@dataclass
class SpeakResult:
    audio_path: str
    duration: float = 0.0
    char_count: int = 0
    cost: float = 0.0
    cached: bool = False


class SpeakClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.audio_key
        self.base_url = (base_url or settings.bodhan_base_url).rstrip("/")
        self.endpoint = f"{self.base_url}/audio/speech"

    @bodhan_retry
    async def synthesize_chunk(
        self,
        text: str,
        voice: str,
        lang: str,
    ) -> bytes:
        """Synthesize a single small chunk of text into WAV audio bytes using Indic-Speak."""
        if not text or not text.strip():
            return b""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        instructions = json.dumps({"lang": lang})
        payload: Dict[str, Any] = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": instructions,
        }

        logger.info("Calling Indic-Speak (voice=%s, lang=%s, chars=%d)", voice, lang, len(text))

        try:
            async with httpx.AsyncClient(timeout=settings.api_timeout_seconds) as client:
                resp = await client.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                )

            if resp.status_code == 200:
                logger.info("Synthesize success (bytes=%d)", len(resp.content))
                return resp.content
            elif resp.status_code == 429:
                raise BodhanError(RATE_LIMITED, "Speak API rate limit exceeded", retryable=True)
            elif resp.status_code in (408, 504):
                raise BodhanError(TIMEOUT, f"Speak API timed out: {resp.text}", retryable=True)
            elif resp.status_code >= 500:
                raise BodhanError(UPSTREAM_ERROR, f"Speak API server error ({resp.status_code}): {resp.text}", retryable=True)
            else:
                raise BodhanError(INVALID_INPUT, f"Speak API error ({resp.status_code}): {resp.text}", retryable=False)

        except httpx.TimeoutException as exc:
            raise BodhanError(TIMEOUT, f"Speak request timed out: {exc}", retryable=True)
        except httpx.RequestError as exc:
            raise BodhanError(UPSTREAM_ERROR, f"Speak network request error: {exc}", retryable=True)

    async def synthesize(
        self,
        text: str,
        voice: str,
        lang: str,
        output_path: Optional[str] = None,
    ) -> SpeakResult:
        """High-level synthesize method handling sentence chunking, concatenation, and caching."""
        if not text or not text.strip():
            raise BodhanError(INVALID_INPUT, "Text must not be empty.")

        cache_params = {"lang": lang, "voice": voice}
        cached = cache_get("indic-speak", cache_params, text)
        if cached and os.path.exists(cached.get("output_file_path", "")):
            return SpeakResult(
                audio_path=cached["output_file_path"],
                duration=cached.get("duration_seconds", 0.0),
                char_count=len(text),
                cost=0.0,
                cached=True,
            )

        sentence_chunks = chunk_text_for_tts(text)
        wav_parts: List[bytes] = []

        for chunk in sentence_chunks:
            part = await self.synthesize_chunk(chunk, voice=voice, lang=lang)
            if part:
                wav_parts.append(part)

        if not wav_parts:
            raise BodhanError(UPSTREAM_ERROR, "No audio was generated.")

        # Concatenate audio chunks
        if len(wav_parts) == 1:
            combined_bytes = wav_parts[0]
        else:
            try:
                from pydub import AudioSegment
                combined = AudioSegment.empty()
                for b in wav_parts:
                    if b:
                        seg = AudioSegment.from_file(io.BytesIO(b), format="wav")
                        combined += seg
                out_buf = io.BytesIO()
                combined.export(out_buf, format="wav")
                combined_bytes = out_buf.getvalue()
            except Exception as e:
                logger.warning("pydub concat fallback: joining raw wav parts (%s)", e)
                combined_bytes = b"".join(wav_parts)

        # Save audio file
        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_bytes(combined_bytes)
            final_audio_path = str(out_p.resolve())
        else:
            final_audio_path = default_file_store.save_bytes(
                combined_bytes, extension=".wav", prefix=f"tts_{voice}"
            )

        duration = get_audio_duration_seconds(final_audio_path)
        cost = calculate_tts_cost(len(text))

        cache_set(
            "indic-speak",
            cache_params,
            text,
            {
                "output_file_path": final_audio_path,
                "duration_seconds": duration,
                "input_chars": len(text),
            },
        )

        return SpeakResult(
            audio_path=final_audio_path,
            duration=duration,
            char_count=len(text),
            cost=cost,
            cached=False,
        )
