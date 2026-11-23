"""indic-speak: POST /v1/audio/speech -> real WAV bytes (PCM16, 24 kHz, mono)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.services.audio.wav import WavFormatError, concat_wavs, parse_wav, wav_duration_seconds
from app.services.bodhan.capabilities import (
    SPEAK_OUTPUT, canonical_style, canonical_voice, ensure_speak_language,
)
from app.services.bodhan.exceptions import BodhanProtocolError, BodhanRequestInvalid
from app.utils.text import split_text

SERVICE = "speak"
MODEL = "indic-speak"


@dataclass
class SpeechSegment:
    index: int
    text: str
    duration_seconds: float
    request_id: str | None = None


@dataclass
class SpeechResult:
    language: str
    voice: str
    style: str | None
    wav_bytes: bytes
    duration_seconds: float
    segments: list[SpeechSegment] = field(default_factory=list)


def build_instructions(language: str, style: str | None) -> str:
    """`instructions` must be a JSON *string*, e.g. '{"lang": "te"}', not a nested object."""
    payload = {"lang": language}
    if style:
        payload["style"] = style
    return json.dumps(payload, ensure_ascii=False)


async def synthesize_speech(client, text: str, language: str, voice: str, *, style: str | None = None,
                            max_chunk_chars: int = 200, gap_ms: int = 150) -> SpeechResult:
    ensure_speak_language(language)
    voice = canonical_voice(voice)
    style = canonical_style(style)
    chunks = split_text(text, max_chunk_chars)
    if not chunks:
        raise BodhanRequestInvalid("There is no text to read aloud.", code="empty_text")
    instructions = build_instructions(language, style)
    wavs: list[bytes] = []
    segments: list[SpeechSegment] = []
    for chunk in chunks:
        resp = await client.request(
            SERVICE, "POST", "/v1/audio/speech",
            json_body={"model": MODEL, "input": chunk.text, "voice": voice, "instructions": instructions},
        )
        is_wav_content_type = resp.content_type in ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave")
        is_riff = resp.content.startswith(b"RIFF")
        if not (is_wav_content_type or is_riff):
            raise BodhanProtocolError(SERVICE, f"Expected audio/wav but received '{resp.content_type or 'unknown'}'.",
                                      status_code=resp.status_code, request_id=resp.request_id)
        try:
            fmt, _ = parse_wav(resp.content)
            duration = wav_duration_seconds(resp.content)
        except WavFormatError as exc:
            raise BodhanProtocolError(SERVICE, f"Response is not a valid WAV file: {exc}",
                                      status_code=resp.status_code, request_id=resp.request_id) from exc
        expected = (SPEAK_OUTPUT["channels"], 2, SPEAK_OUTPUT["sample_rate_hz"])
        if (fmt.channels, fmt.sample_width, fmt.frame_rate) != expected:
            # Documented as PCM16/24kHz/mono. Different is allowed only if every segment agrees
            # (concat_wavs enforces that); we surface it instead of hiding it.
            import logging
            logging.getLogger("heritagelens.bodhan").warning(
                "speech audio format differs from documentation: %s", fmt)
        wavs.append(resp.content)
        segments.append(SpeechSegment(chunk.index, chunk.text, duration, resp.request_id))
    try:
        joined = concat_wavs(wavs, gap_ms=gap_ms)
    except WavFormatError as exc:
        raise BodhanProtocolError(SERVICE, str(exc)) from exc
    return SpeechResult(language, voice, style, joined, wav_duration_seconds(joined), segments)
