"""Text-to-Speech tool wrapping Indic-Speak."""
from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import Optional

from bodhan_mcp.clients.speak import SpeakClient
from bodhan_mcp.config import settings
from bodhan_mcp.core.cache import cache_get, cache_set
from bodhan_mcp.core.chunking import chunk_text_for_tts, get_audio_duration_seconds
from bodhan_mcp.core.cost import calculate_tts_cost
from bodhan_mcp.core.errors import BodhanError, INVALID_INPUT, UNSUPPORTED_LANGUAGE
from bodhan_mcp.core.file_store import default_file_store
from bodhan_mcp.schemas import SynthesizeSpeechOutput

logger = logging.getLogger(__name__)


def _concat_wav_bytes(chunks_bytes: list[bytes]) -> bytes:
    """Concatenate multiple WAV byte streams into a single WAV byte stream."""
    if not chunks_bytes:
        return b""
    if len(chunks_bytes) == 1:
        return chunks_bytes[0]

    try:
        from pydub import AudioSegment
        combined = AudioSegment.empty()
        for b in chunks_bytes:
            if b:
                seg = AudioSegment.from_file(io.BytesIO(b), format="wav")
                combined += seg
        out_buf = io.BytesIO()
        combined.export(out_buf, format="wav")
        return out_buf.getvalue()
    except Exception as exc:
        logger.warning("pydub concat failed (%s), returning joined raw audio", exc)
        return b"".join(chunks_bytes)


def resolve_voice(voice_input: str, language: str) -> str:
    """
    Extracts and resolves the voice according to user need/gender,
    handling descriptors like 'female', 'male', case differences, or explicit voice names.
    """
    lang = language.lower()
    valid_voices = settings.tts_voices.get(lang, ["Amit", "Priya"])
    if not voice_input:
        return valid_voices[0]

    v_str = str(voice_input).strip()
    v_lower = v_str.lower()

    # 1. Exact case-insensitive match
    for v in valid_voices:
        if v_lower == v.lower():
            return v

    # 2. Gender extraction
    female_terms = ["female", "femal", "woman", "girl", "lady", "feminine"]
    male_terms = ["male", "man", "boy", "gentleman", "masculine"]

    if any(t in v_lower for t in female_terms):
        return settings.tts_female_voices.get(lang, valid_voices[-1])
    if any(t in v_lower for t in male_terms):
        return settings.tts_male_voices.get(lang, valid_voices[0])

    # 3. Substring matching in valid voices for this language
    for v in valid_voices:
        if v.lower() in v_lower or v_lower in v.lower():
            return v

    # 4. Check if voice name matches a known voice in another language, map by gender
    for l_code, f_voice in settings.tts_female_voices.items():
        if v_lower == f_voice.lower():
            return settings.tts_female_voices.get(lang, valid_voices[-1])
    for l_code, m_voice in settings.tts_male_voices.items():
        if v_lower == m_voice.lower():
            return settings.tts_male_voices.get(lang, valid_voices[0])

    # 5. Default fallback to language default voice
    logger.warning("Unrecognized voice '%s' for '%s'; defaulting to '%s'", voice_input, lang, valid_voices[0])
    return valid_voices[0]


async def synthesize_speech(
    text: str,
    language: str,
    voice: str,
    output_path: Optional[str] = None,
) -> SynthesizeSpeechOutput:
    """Synthesize speech in an Indic language using Indic-Speak.
    
    Splits long text across sentence boundaries to guarantee synthesis quality.
    Voices:
      hi: Amit, Priya
      bn: Ravi, Ananya
      ta: Surya, Devi
      te: Arjun, Kavya
      mr: Vikram, Riya
      gu: Kabir, Nisha
      kn: Kiran, Deepa
      ml: Dev, Meera
      pa: Jeet, Simran
    """
    t0 = time.time()

    if not text or not text.strip():
        raise BodhanError(INVALID_INPUT, "Text must not be empty.")

    lang = str(language).lower()
    if lang not in settings.tts_languages:
        raise BodhanError(
            UNSUPPORTED_LANGUAGE,
            f"Language '{language}' is not supported by Indic-Speak. "
            f"Supported: {sorted(list(settings.tts_languages))}",
            retryable=False,
        )

    valid_voices = settings.tts_voices.get(lang, [])
    voice = resolve_voice(voice, lang)

    # Check cache
    cache_params = {"lang": lang, "voice": voice}
    cached_val = cache_get("indic-speak", cache_params, text)
    if cached_val:
        logger.info("Cache hit for TTS voice=%s lang=%s", voice, lang)
        cached_val["cached"] = True
        cached_val["latency_seconds"] = round(time.time() - t0, 3)
        cached_val["cost_inr"] = 0.0
        return SynthesizeSpeechOutput(**cached_val)

    client = SpeakClient()
    sentence_chunks = chunk_text_for_tts(text)

    wav_parts: list[bytes] = []
    used_fallback_voice = False
    fallback_note = ""

    try:
        for chunk in sentence_chunks:
            wav_data = await client.synthesize_chunk(chunk, voice=voice, lang=lang)
            if wav_data:
                wav_parts.append(wav_data)
    except BodhanError as exc:
        if exc.code == "UPSTREAM_ERROR":
            # Upstream Bodhan API server error (500) on this specific voice/lang combination.
            # Automatically try fallback voice so synthesis completes successfully.
            # Prioritize matching gender in fallbacks
            is_male_requested = any(voice in v for v in settings.tts_male_voices.values())
            if is_male_requested:
                preferred = [v for v in valid_voices if v != voice and v in settings.tts_male_voices.get(lang, [])] + ["Amit"]
                others = [v for v in valid_voices if v != voice and v not in preferred] + ["Priya"]
            else:
                preferred = [v for v in valid_voices if v != voice and v in settings.tts_female_voices.get(lang, [])] + ["Priya"]
                others = [v for v in valid_voices if v != voice and v not in preferred] + ["Amit"]

            candidate_fallbacks = []
            for v in preferred + others:
                if v not in candidate_fallbacks and v != voice:
                    candidate_fallbacks.append(v)

            for fb_voice in candidate_fallbacks:
                logger.warning(
                    "Upstream Indic-Speak error (500) on voice '%s'. Retrying with fallback voice '%s'...",
                    voice,
                    fb_voice,
                )
                try:
                    wav_parts = []
                    for chunk in sentence_chunks:
                        wav_data = await client.synthesize_chunk(chunk, voice=fb_voice, lang=lang)
                        if wav_data:
                            wav_parts.append(wav_data)
                    voice = fb_voice
                    used_fallback_voice = True
                    fallback_note = f"Primary voice had upstream server issue; fell back to voice '{fb_voice}'."
                    break
                except Exception as fb_exc:
                    logger.warning("Fallback voice '%s' also failed: %s", fb_voice, fb_exc)
            if not wav_parts:
                raise exc
        else:
            raise exc

    final_wav = _concat_wav_bytes(wav_parts)
    if not final_wav:
        raise BodhanError(INVALID_INPUT, "No audio generated from synthesis.")

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(final_wav)
        final_file_path = str(out_p.resolve())
    else:
        final_file_path = default_file_store.save_bytes(final_wav, extension=".wav", prefix="tts")

    duration = get_audio_duration_seconds(final_file_path)
    cost = calculate_tts_cost(len(text))
    latency = round(time.time() - t0, 3)

    warning_msg = "AI-generated audio. Bodhan Indic-Speak synthesis."
    if used_fallback_voice and fallback_note:
        warning_msg += f" Note: {fallback_note}"

    output = SynthesizeSpeechOutput(
        ok=True,
        output_file_path=final_file_path,
        duration_seconds=round(duration, 2),
        input_chars=len(text),
        chunks_synthesized=len(sentence_chunks),
        cost_inr=cost,
        latency_seconds=latency,
        cached=False,
        warning=warning_msg,
    )

    cache_set("indic-speak", cache_params, text, output.model_dump())
    return output
