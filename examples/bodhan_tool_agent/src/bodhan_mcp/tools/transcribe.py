"""Bodhan Speech-to-Text tool using indic-transcribe."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
import httpx

from bodhan_mcp.config import (
    BODHAN_API_KEY,
    BODHAN_BASE_URL,
    BODHAN_TRANSCRIBE_API_KEY,
    MAX_AUDIO_CHUNK_SECONDS,
    MODEL_TRANSCRIBE,
    PRICING,
    TRANSCRIBE_LANGUAGES,
)
from bodhan_mcp.errors import (
    BodhanAPIError,
    BodhanAuthenticationError,
    BodhanRateLimitError,
    InvalidInputError,
    UnsupportedLanguageError,
)
from bodhan_mcp.schemas import AudioChunkTranscript, TranscribeAudioOutput
from bodhan_mcp.tools.file_utils import ALLOWED_AUDIO_EXTENSIONS, resolve_safe_path
from bodhan_mcp.tools.media import (
    chunk_wav_file,
    convert_to_wav,
    get_audio_duration_seconds,
)


def _call_transcribe_chunk(
    client: httpx.Client,
    audio_blob: bytes,
    filename: str,
    language: str | None,
    api_key: str,
    base_url: str,
    max_retries: int = 3,
) -> str:
    """Call POST /v1/audio/transcriptions for a single audio chunk with exponential backoff."""
    url = f"{base_url}/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    data = {"model": MODEL_TRANSCRIBE}
    if language:
        data["language"] = language

    files = {"file": (filename, audio_blob, "audio/wav")}

    backoff = 1.0
    for attempt in range(max_retries):
        try:
            resp = client.post(url, headers=headers, data=data, files=files, timeout=60.0)
            if resp.status_code == 200:
                payload = resp.json()
                return payload.get("text", "").strip()

            if resp.status_code == 401:
                raise BodhanAuthenticationError("Invalid or missing BODHAN_API_KEY")

            if resp.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise BodhanRateLimitError("Rate limit exceeded for transcription")

            # Handle 5xx
            if resp.status_code >= 500:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise BodhanAPIError(resp.status_code, f"Server error from Bodhan API: {resp.text}")

            # Other 4xx
            raise BodhanAPIError(resp.status_code, f"Transcription API failed: {resp.text}")

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2.0
                continue
            raise BodhanAPIError(504, f"Network timeout during transcription: {e}")

    return ""


def transcribe_audio(
    file_path: str,
    language: str | None = None,
    timestamp_granularities: list[str] | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> TranscribeAudioOutput:
    """Transcribe an audio file using Bodhan indic-transcribe.

    Validates audio format, inspects audio duration, chunks audio longer than 28 seconds,
    transcribes each chunk, and reconstructs the transcript in sequence.
    """
    key = api_key or os.getenv("BODHAN_TRANSCRIBE_API_KEY") or os.getenv("BODHAN_API_KEY") or BODHAN_TRANSCRIBE_API_KEY or BODHAN_API_KEY
    if not key:
        raise BodhanAuthenticationError("BODHAN_API_KEY is not set")
    api_root = (base_url or os.getenv("BODHAN_BASE_URL") or BODHAN_BASE_URL).rstrip("/")

    # Safe path resolution
    resolved_path = resolve_safe_path(file_path, allowed_extensions=ALLOWED_AUDIO_EXTENSIONS)

    # Validate language if specified
    if language:
        lang_clean = language.strip().lower()
        if lang_clean not in TRANSCRIBE_LANGUAGES:
            raise UnsupportedLanguageError(lang_clean, "speech transcription", list(TRANSCRIBE_LANGUAGES))
        language = lang_clean

    duration = get_audio_duration_seconds(resolved_path)
    if duration <= 0.05:
        raise InvalidInputError("Audio file has no detectable audio content or is zero-length")

    cost = round(duration * PRICING.transcribe_per_second_inr, 4)

    # Prepare WAV file
    temp_wav = None
    if resolved_path.suffix.lower() == ".wav":
        wav_path = resolved_path
    else:
        temp_handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_wav = Path(temp_handle.name)
        temp_handle.close()
        wav_path = convert_to_wav(resolved_path, temp_wav)

    chunks_data: list[AudioChunkTranscript] = []
    chunk_texts: list[str] = []

    try:
        with httpx.Client() as client:
            # If duration <= MAX_AUDIO_CHUNK_SECONDS, transcribe directly
            if duration <= MAX_AUDIO_CHUNK_SECONDS:
                raw_bytes = wav_path.read_bytes()
                text = _call_transcribe_chunk(
                    client=client,
                    audio_blob=raw_bytes,
                    filename=wav_path.name,
                    language=language,
                    api_key=key,
                    base_url=api_root,
                )
                chunk_texts.append(text)
                chunks_data.append(
                    AudioChunkTranscript(chunk_index=0, start_time_s=0.0, end_time_s=duration, text=text)
                )
            else:
                for idx, start_s, end_s, blob in chunk_wav_file(wav_path, chunk_seconds=MAX_AUDIO_CHUNK_SECONDS):
                    text = _call_transcribe_chunk(
                        client=client,
                        audio_blob=blob,
                        filename=f"chunk_{idx:03d}.wav",
                        language=language,
                        api_key=key,
                        base_url=api_root,
                    )
                    if text:
                        chunk_texts.append(text)
                    chunks_data.append(
                        AudioChunkTranscript(chunk_index=idx, start_time_s=start_s, end_time_s=end_s, text=text)
                    )

        full_text = " ".join(t for t in chunk_texts if t).strip()
        detected_or_req_lang = language or "auto"

        return TranscribeAudioOutput(
            success=True,
            text=full_text,
            language=detected_or_req_lang,
            duration_seconds=round(duration, 2),
            chunks=chunks_data,
            estimated_cost_inr=cost,
        )

    finally:
        if temp_wav and temp_wav.exists():
            try:
                temp_wav.unlink()
            except Exception:
                pass
