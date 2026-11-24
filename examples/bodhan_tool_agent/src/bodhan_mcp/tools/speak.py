"""Bodhan Text-to-Speech tool using indic-speak."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
import httpx

from bodhan_mcp.config import (
    BODHAN_API_KEY,
    BODHAN_BASE_URL,
    BODHAN_SPEAK_API_KEY,
    MAX_TTS_SENTENCE_CHARS,
    MODEL_SPEAK,
    PRICING,
    SPEAK_LANGUAGES,
    SPEAK_STYLES,
    SPEAK_VOICES,
)
from bodhan_mcp.errors import (
    BodhanAPIError,
    BodhanAuthenticationError,
    BodhanRateLimitError,
    InvalidInputError,
    UnsupportedLanguageError,
    UnsupportedVoiceError,
)
from bodhan_mcp.schemas import SynthesizeSpeechOutput
from bodhan_mcp.tools.media import get_audio_duration_seconds, stitch_wav_bytes

SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[।.!?])\s+")


def _split_text_for_speech(text: str, max_chars: int = MAX_TTS_SENTENCE_CHARS) -> list[str]:
    """Split text cleanly on sentence boundaries for high-quality speech synthesis."""
    raw_sentences = [s.strip() for s in SENTENCE_SPLIT_PATTERN.split(text.strip()) if s.strip()]
    final_sentences = []

    for s in raw_sentences:
        if len(s) <= max_chars:
            final_sentences.append(s)
        else:
            # Sub-split on commas or conjunctions if a single sentence is exceedingly long
            parts = [p.strip() for p in re.split(r"[,;:\n]+", s) if p.strip()]
            cur = ""
            for p in parts:
                if len(cur) + len(p) + 2 <= max_chars:
                    cur = f"{cur}, {p}" if cur else p
                else:
                    if cur:
                        final_sentences.append(cur)
                    cur = p
            if cur:
                final_sentences.append(cur)

    return [s for s in final_sentences if s]


def _call_speech_chunk(
    client: httpx.Client,
    text: str,
    voice: str,
    language: str,
    style: str | None,
    api_key: str,
    base_url: str,
    max_retries: int = 3,
) -> bytes:
    """Call POST /v1/audio/speech with retry and backoff."""
    url = f"{base_url}/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    instructions_dict = {"lang": language}
    if style:
        instructions_dict["style"] = style

    body = {
        "model": MODEL_SPEAK,
        "input": text,
        "voice": voice,
        "instructions": json.dumps(instructions_dict),
    }

    backoff = 1.0
    for attempt in range(max_retries):
        try:
            resp = client.post(url, headers=headers, json=body, timeout=120.0)
            if resp.status_code == 200 and resp.content:
                return resp.content

            if resp.status_code == 401:
                raise BodhanAuthenticationError("Invalid or missing BODHAN_API_KEY")

            if resp.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise BodhanRateLimitError("Rate limit exceeded for speech synthesis")

            if resp.status_code >= 500:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise BodhanAPIError(resp.status_code, f"Server error from Bodhan API: {resp.text}")

            raise BodhanAPIError(resp.status_code, f"Speech API failed: {resp.text}")

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2.0
                continue
            raise BodhanAPIError(504, f"Network timeout during speech synthesis: {e}")

    return b""


def synthesize_speech(
    text: str,
    language: str,
    voice: str | None = None,
    gender_preference: str = "female",
    style: str | None = None,
    output_file_path: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> SynthesizeSpeechOutput:
    """Synthesize text into natural speech using Bodhan indic-speak.

    Validates language and voice compatibility, splits text into sentence units,
    calls the Bodhan speech synthesis API, stitches audio chunks into a valid WAV file,
    and returns metadata with an AI-generated notice.
    """
    key = api_key or os.getenv("BODHAN_SPEAK_API_KEY") or os.getenv("BODHAN_API_KEY") or BODHAN_SPEAK_API_KEY or BODHAN_API_KEY
    if not key:
        raise BodhanAuthenticationError("BODHAN_API_KEY is not set")
    api_root = (base_url or os.getenv("BODHAN_BASE_URL") or BODHAN_BASE_URL).rstrip("/")

    if not text or not text.strip():
        raise InvalidInputError("Input text to synthesize cannot be empty", field="text")

    lang_clean = language.strip().lower()
    if lang_clean not in SPEAK_LANGUAGES:
        raise UnsupportedLanguageError(lang_clean, "speech synthesis", list(SPEAK_LANGUAGES))

    # Determine voice
    voices_for_lang = SPEAK_VOICES.get(lang_clean, SPEAK_VOICES.get("en", {}))
    all_voices = voices_for_lang.get("female", []) + voices_for_lang.get("male", [])

    selected_voice = voice
    if selected_voice:
        # Check if requested voice is valid for this language
        match = next((v for v in all_voices if v.lower() == selected_voice.lower()), None)
        if not match:
            raise UnsupportedVoiceError(selected_voice, lang_clean, all_voices)
        selected_voice = match
    else:
        # Pick default based on gender preference
        pref_list = voices_for_lang.get(gender_preference.lower(), voices_for_lang.get("female", []))
        selected_voice = pref_list[0] if pref_list else all_voices[0]

    # Validate style if supplied
    if style:
        style_match = next((s for s in SPEAK_STYLES if s.lower() == style.lower()), None)
        if not style_match:
            raise InvalidInputError(
                f"Invalid style '{style}'. Supported styles: {', '.join(SPEAK_STYLES)}",
                field="style",
            )
        style = style_match

    # Output destination
    if output_file_path:
        dest_path = Path(output_file_path).resolve()
    else:
        timestamp = int(time.time())
        dest_path = Path("outputs") / f"speech_{lang_clean}_{timestamp}.wav"
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    sentences = _split_text_for_speech(text)
    audio_clips: list[bytes] = []

    with httpx.Client() as client:
        for s in sentences:
            clip = _call_speech_chunk(
                client=client,
                text=s,
                voice=selected_voice,
                language=lang_clean,
                style=style,
                api_key=key,
                base_url=api_root,
            )
            if clip:
                audio_clips.append(clip)

    if not audio_clips:
        raise BodhanAPIError(500, "Speech synthesis produced no audio data")

    # Stitch clips into a single continuous WAV
    final_path = stitch_wav_bytes(audio_clips, dest_path)
    duration = get_audio_duration_seconds(final_path)
    cost = round(duration * PRICING.speak_per_second_inr, 4)

    return SynthesizeSpeechOutput(
        success=True,
        audio_file_path=str(final_path),
        duration_seconds=round(duration, 2),
        language=lang_clean,
        voice=selected_voice,
        style=style,
        estimated_cost_inr=cost,
    )
