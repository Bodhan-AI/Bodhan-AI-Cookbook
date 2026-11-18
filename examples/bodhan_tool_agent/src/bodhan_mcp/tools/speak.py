"""Indic-Speak tool for Bodhan MCP Server.

Wraps Bodhan AI's Indic-Speak text-to-speech model (POST https://api.bodhan.ai/v1/audio/speech)
with sentence-based chunking (max 250 chars), hard cost budget enforcement, local per-chunk
disk caching, strict pre-flight language and voice validation, preview tier warning,
multi-chunk ffmpeg concatenation, and audio verification (4 to 25 chars/sec).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from pydantic import BaseModel, Field

from bodhan_mcp.common import (
    BODHAN_API_BASE,
    CallTracker,
    StructuredError,
    calculate_speak_cost_inr,
    classify_http_response,
    get_cache_key,
    check_live_calls_allowed,
    check_global_cap_allowed,
    is_mock_transport,
    log_spend_dispatched,
    update_spend_ledger,
    log_spend_cache_hit,
)
from bodhan_mcp.tools.transcribe import get_ffmpeg_binaries, probe_audio_duration

# ------------------------------------------------------------------------------
# Constants & Language/Voice Registry
# ------------------------------------------------------------------------------

MODEL_NAME = "indic-speak"
CACHE_DIR = Path(".cache/bodhan_speak")
DEFAULT_MAX_COST_INR = 0.50
MAX_CHUNK_CHARS = 250  # Documented assumption: "a sentence or two, about 30 seconds"

# Speech verification rate: 4 to 25 characters per second
MIN_SPEECH_CHARS_PER_SEC = 4.0
MAX_SPEECH_CHARS_PER_SEC = 25.0

# 13 Production Tier Languages
PRODUCTION_LANGUAGES = {
    "as",
    "bn",
    "en",
    "gu",
    "hi",
    "kn",
    "ml",
    "mr",
    "or",
    "pa",
    "ta",
    "te",
    "ur",
}

# 10 Preview Tier Languages
PREVIEW_LANGUAGES = {"brx", "doi", "kok", "ks", "mai", "mni", "ne", "sa", "sat", "sd"}

ALL_SUPPORTED_LANGUAGES = PRODUCTION_LANGUAGES | PREVIEW_LANGUAGES

# Recognized Voice Catalog per language
VOICES_BY_LANGUAGE: Dict[str, List[str]] = {
    "as": ["Prastuti", "Ankur"],
    "bn": ["Ishita", "Sourav"],
    "brx": ["Gwrbw", "Sansuma"],
    "doi": ["Preeti", "Sham"],
    "en": ["Kavya", "Amit"],
    "gu": ["Dhara", "Parth"],
    "hi": ["Kavya", "Suhani", "Amit"],
    "kn": ["Ananya", "Chethan"],
    "ks": ["Mehak", "Danish"],
    "kok": ["Sanjana", "Sahil"],
    "mai": ["Aarti", "Nitin"],
    "ml": ["Kiran", "Lakshmi"],
    "mni": ["Chaoba", "Thoibi"],
    "mr": ["Anagha", "Chinmay"],
    "ne": ["Sagar", "Srijana"],
    "or": ["Akash", "Itishree"],
    "pa": ["Kaur", "Manpreet"],
    "sa": ["Aryaman", "Bharati"],
    "sat": ["Phulmani", "Sibu"],
    "sd": ["Moomal", "Rano"],
    "ta": ["Anitha", "Arun"],
    "te": ["Sravani", "Vamsi"],
    "ur": ["Saba", "Zaid"],
}

ALL_VALID_VOICES = {voice for voices in VOICES_BY_LANGUAGE.values() for voice in voices}

# First documented female voice for deterministic caching defaults
DEFAULT_FEMALE_VOICES: Dict[str, str] = {
    "as": "Prastuti",
    "bn": "Ishita",
    "brx": "Gwrbw",
    "doi": "Preeti",
    "en": "Kavya",
    "gu": "Dhara",
    "hi": "Kavya",
    "kn": "Ananya",
    "ks": "Mehak",
    "kok": "Sanjana",
    "mai": "Aarti",
    "ml": "Lakshmi",
    "mni": "Thoibi",
    "mr": "Anagha",
    "ne": "Srijana",
    "or": "Itishree",
    "pa": "Kaur",
    "sa": "Bharati",
    "sat": "Phulmani",
    "sd": "Moomal",
    "ta": "Anitha",
    "te": "Sravani",
    "ur": "Saba",
}

# First documented male voice per language
DEFAULT_MALE_VOICES: Dict[str, str] = {
    "as": "Ankur",
    "bn": "Sourav",
    "brx": "Sansuma",
    "doi": "Sham",
    "en": "Amit",
    "gu": "Parth",
    "hi": "Amit",
    "kn": "Chethan",
    "ks": "Danish",
    "kok": "Sahil",
    "mai": "Nitin",
    "ml": "Kiran",
    "mni": "Chaoba",
    "mr": "Chinmay",
    "ne": "Sagar",
    "or": "Akash",
    "pa": "Manpreet",
    "sa": "Aryaman",
    "sat": "Sibu",
    "sd": "Rano",
    "ta": "Arun",
    "te": "Vamsi",
    "ur": "Zaid",
}


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------


def detect_audio_format(data: bytes) -> str:
    """Detects audio format from magic bytes:
    - RIFF....WAVE: wav
    - ID3... or 0xFFFx sync word: mp3
    Returns 'wav', 'mp3', or 'unknown'. Never trusts Content-Type header.
    """
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"ID3"):
        return "mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"
    return "unknown"


def is_target_pcm_wav(
    data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    bit_depth: int = 16,
) -> bool:
    """Checks if audio data is already the target uncompressed 24 kHz mono 16-bit PCM WAV."""
    if detect_audio_format(data) != "wav":
        return False
    try:
        import io
        import wave

        with wave.open(io.BytesIO(data), "rb") as wf:
            return (
                wf.getframerate() == sample_rate
                and wf.getnchannels() == channels
                and wf.getsampwidth() == (bit_depth // 8)
                and wf.getcomptype() == "NONE"
            )
    except Exception:
        return False


def chunk_text_by_sentence(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """Chunks text into segments <= max_chars (default 250) along sentence boundaries.

    If a single sentence exceeds max_chars, it splits at word boundaries (spaces).
    Documented assumption: The Bodhan docs recommend 'a sentence or two, about 30 seconds'.
    A safe limit of 250 characters ensures optimal synthesis quality and avoids server timeouts.
    """
    clean_text = text.strip()
    if not clean_text:
        return []

    sentence_pattern = re.compile(r"([^.!?।\n]+[.!?।\n]*)", re.UNICODE)
    raw_segments = [s.strip() for s in sentence_pattern.findall(clean_text) if s.strip()]
    if not raw_segments:
        raw_segments = [clean_text]

    chunks: List[str] = []
    current_chunk = ""

    for seg in raw_segments:
        if len(seg) > max_chars:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            words = seg.split(" ")
            sub_chunk = ""
            for w in words:
                if not w:
                    continue
                test_sub = f"{sub_chunk} {w}".strip() if sub_chunk else w
                if len(test_sub) <= max_chars:
                    sub_chunk = test_sub
                else:
                    if sub_chunk:
                        chunks.append(sub_chunk)
                    sub_chunk = w
            if sub_chunk:
                chunks.append(sub_chunk)
        else:
            test_chunk = f"{current_chunk} {seg}".strip() if current_chunk else seg
            if len(test_chunk) <= max_chars:
                current_chunk = test_chunk
            else:
                chunks.append(current_chunk.strip())
                current_chunk = seg

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def concatenate_wav_chunks(chunk_files: List[Path], output_path: Path, ffmpeg_bin: str) -> None:
    """Concatenates and transcodes audio chunk files into a single WAV re-encoded to 24 kHz mono PCM16.

    If there is only 1 chunk and it is already the target 24 kHz mono 16-bit PCM WAV,
    it avoids running ffmpeg and copies directly. Otherwise, ffmpeg is used to convert.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(chunk_files) == 1:
        raw_bytes = chunk_files[0].read_bytes()
        if is_target_pcm_wav(raw_bytes):
            shutil.copy2(chunk_files[0], output_path)
            return

        cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-i",
            str(chunk_files[0]),
            "-c:a",
            "pcm_s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
        return

    inputs: List[str] = []
    filter_inputs = ""
    for idx, cf in enumerate(chunk_files):
        inputs.extend(["-i", str(cf)])
        filter_inputs += f"[{idx}:a]"

    filter_complex = f"{filter_inputs}concat=n={len(chunk_files)}:v=0:a=1[outa]"
    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[outa]",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "24000",
        "-ac",
        "1",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=60)


def verify_speech_audio(
    audio_path: Path, char_count: int, ffprobe_bin: Optional[str]
) -> Tuple[bool, str, float]:
    """Verifies generated speech audio using ffprobe.

    Checks:
    1. Output audio exists and duration > 0.0s.
    2. Duration is plausible for the character count at 4 to 25 characters per second.
    """
    if not audio_path.exists():
        return False, f"Output audio file does not exist: {audio_path}", 0.0

    if not ffprobe_bin:
        return False, "ffprobe binary not available for verification.", 0.0

    duration, err = probe_audio_duration(audio_path, ffprobe_bin)
    if err or duration is None or duration <= 0.0:
        return False, f"ffprobe duration verification failed: {err.message if err else '0.0s'}", 0.0

    min_plausible_duration = char_count / MAX_SPEECH_CHARS_PER_SEC
    max_plausible_duration = char_count / MIN_SPEECH_CHARS_PER_SEC

    if not (min_plausible_duration <= duration <= max_plausible_duration):
        return (
            False,
            f"Duration ({duration:.2f}s) outside plausible range [4-25 chars/s: {min_plausible_duration:.2f}s - {max_plausible_duration:.2f}s] for {char_count} chars.",
            duration,
        )

    return (
        True,
        f"Audio verified: duration {duration:.2f}s is within plausible range [4-25 chars/s].",
        duration,
    )


# ------------------------------------------------------------------------------
# Pydantic Schemas
# ------------------------------------------------------------------------------


class SpeakChunkResult(BaseModel):
    chunk_index: int = Field(..., description="1-indexed chunk sequence.")
    text: str = Field(..., description="Text segment synthesized in this chunk.")
    characters: int = Field(..., description="Character count of this chunk.")
    cache_hit: bool = Field(
        False, description="Whether this chunk was served from local disk cache."
    )
    error: Optional[StructuredError] = Field(
        None, description="Structured error if chunk synthesis failed."
    )


class SpeakInput(BaseModel):
    input_text: Optional[str] = Field(
        None,
        description="Text string or path to a .txt file. Never pass LLM-invented Indian language text.",
    )
    text: Optional[str] = Field(
        None,
        description="Alias for input_text.",
    )
    voice: Optional[str] = Field(
        None, description="Name of the voice profile (e.g. 'Kavya', 'Amit')."
    )
    language: str = Field(..., description="ISO language code of the text (e.g. 'hi', 'en', 'ta').")
    output_path: str = Field(
        ..., description="Destination file path where the generated WAV audio must be saved."
    )
    max_cost_inr: float = Field(
        DEFAULT_MAX_COST_INR,
        description=f"Per-call budget cap in INR (default Rs {DEFAULT_MAX_COST_INR:.2f}).",
    )


class SpeakOutput(BaseModel):
    success: bool
    output_file_path: str = ""
    character_count: int = 0
    chunks_count: int = 0
    cached_chunks_count: int = 0
    billed_chunks_count: int = 0
    duration_seconds: float = 0.0
    verified: bool = False
    verification_reason: str = ""
    warning: Optional[str] = None
    voice_gender_source: str = Field(
        "inferred",
        description="Voice gender is inferred from conventional names (docs list names without gender) and has not been verified by listening.",
    )
    estimated_cost_inr: float = 0.0
    chunks: List[SpeakChunkResult] = []
    error: Optional[StructuredError] = None


# ------------------------------------------------------------------------------
# Tool Implementation
# ------------------------------------------------------------------------------


class IndicSpeakTool:
    """Manages text-to-speech calls to Bodhan AI's indic-speak model."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BODHAN_API_BASE,
        cache_dir: Path = CACHE_DIR,
        client: Optional[httpx.AsyncClient] = None,
        max_cost_inr: float = DEFAULT_MAX_COST_INR,
        ffmpeg_bin: Optional[str] = None,
        ffprobe_bin: Optional[str] = None,
        run_id: Optional[str] = None,
        ledger_path: Optional[Union[str, Path]] = None,
    ):
        self.api_key = api_key or os.getenv("BODHAN_SPEAK_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.custom_client = client
        self.max_cost_inr = max_cost_inr
        self.semaphore = asyncio.Semaphore(1)  # Concurrency 1
        self.run_id = run_id or os.getenv("BODHAN_RUN_ID", "default")
        self.ledger_path = ledger_path

        # Binary discovery
        disc_ffmpeg, disc_ffprobe = get_ffmpeg_binaries()
        self.ffmpeg_bin = ffmpeg_bin or disc_ffmpeg
        self.ffprobe_bin = ffprobe_bin or disc_ffprobe

    def _resolve_text(self, text_or_path: str) -> Tuple[str, Optional[StructuredError]]:
        """Resolves input string or reads from .txt file."""
        if not text_or_path or not text_or_path.strip():
            return "", StructuredError(
                code="EMPTY_INPUT", message="Input text is empty or whitespace-only."
            )

        potential_path = Path(text_or_path.strip())
        if (
            potential_path.suffix.lower() == ".txt"
            and potential_path.exists()
            and potential_path.is_file()
        ):
            try:
                content = potential_path.read_text(encoding="utf-8").strip()
                if not content:
                    return "", StructuredError(
                        code="EMPTY_INPUT", message=f"Input file '{text_or_path}' is empty."
                    )
                return content, None
            except Exception as e:
                return "", StructuredError(
                    code="FILE_READ_ERROR", message=f"Failed to read input file: {str(e)}"
                )

        return text_or_path.strip(), None

    def estimate_cost(
        self,
        text_or_path: str,
        language: str,
        voice: str,
        max_cost_inr: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Pre-flight cost estimation based on character count without calling the API."""
        text, err = self._resolve_text(text_or_path)
        if err or not text:
            return {
                "character_count": 0,
                "chunks_count": 0,
                "estimated_cost_inr": 0.0,
                "error": err.message if err else "Empty text",
            }

        char_count = len(text)
        chunks = chunk_text_by_sentence(text)
        cost = calculate_speak_cost_inr(char_count)
        budget = max_cost_inr if max_cost_inr is not None else self.max_cost_inr
        budget_exceeded = cost > budget

        return {
            "character_count": char_count,
            "chunks_count": len(chunks),
            "estimated_cost_inr": cost,
            "budget_limit_inr": budget,
            "budget_exceeded": budget_exceeded,
        }

    async def _speak_single_chunk(
        self,
        client: httpx.AsyncClient,
        chunk_text: str,
        voice: str,
        language: str,
        call_tracker: CallTracker,
    ) -> Tuple[Optional[bytes], Optional[StructuredError]]:
        """Sends a single <=250-char chunk to POST /v1/audio/speech with max 1 retry."""
        url = f"{self.base_url}/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Exact payload structure matching Bodhan documentation
        payload = {
            "model": MODEL_NAME,
            "input": chunk_text,
            "voice": voice,
            "instructions": json.dumps({"lang": language}),
        }

        # Live-call safety guard: block real HTTP unless BODHAN_ALLOW_LIVE=1
        guard_err = check_live_calls_allowed(url, client)
        if guard_err:
            return None, guard_err

        # Global spend cap guard
        est_cost = calculate_speak_cost_inr(len(chunk_text))
        cap_err = check_global_cap_allowed(est_cost, self.ledger_path)
        if cap_err:
            return None, cap_err

        max_retries = 1  # At most 1 retry, only on 429 or 5xx
        response_2xx: Optional[httpx.Response] = None
        last_err: Optional[StructuredError] = None
        call_id: Optional[str] = None

        for attempt in range(max_retries + 1):
            if not call_tracker.record_call():
                return None, StructuredError(
                    code="CALL_LIMIT_EXCEEDED",
                    message=f"Exceeded maximum allowed HTTP calls ({call_tracker.max_calls}) for this invocation.",
                    retryable=False,
                )

            # Write ledger line with status "dispatched" BEFORE real HTTP send
            call_id = None
            if not is_mock_transport(client) or os.getenv("BODHAN_RECORD_MOCK_SPEND") == "1":
                call_id = log_spend_dispatched(
                    model=MODEL_NAME,
                    estimated_cost_inr=est_cost,
                    caller="indic_speak",
                    run_id=self.run_id,
                    ledger_path=self.ledger_path,
                )

            try:
                async with self.semaphore:
                    response = await client.post(url, headers=headers, json=payload, timeout=60.0)

                # Check if 2xx response was received
                if 200 <= response.status_code < 300:
                    response_2xx = response
                    break

                # Never retry on 4xx (except 429)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    err = StructuredError(
                        code=f"HTTP_{response.status_code}",
                        message=f"Client error from Bodhan Indic-Speak (HTTP {response.status_code}): {response.text}",
                        retryable=False,
                    )
                    if call_id:
                        update_spend_ledger(
                            call_id=call_id,
                            status="failed",
                            http_status=response.status_code,
                            error_code=err.code,
                            ledger_path=self.ledger_path,
                        )
                    return None, err

                should_retry, sleep_s, err = classify_http_response(response, attempt, max_retries)
                last_err = err
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        http_status=response.status_code,
                        error_code=err.code if err else f"HTTP_{response.status_code}",
                        ledger_path=self.ledger_path,
                    )
                if should_retry:
                    await asyncio.sleep(sleep_s)
                    continue
                return None, err

            except httpx.TimeoutException:
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        error_code="TIMEOUT",
                        ledger_path=self.ledger_path,
                    )
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                    continue
                return None, StructuredError(
                    code="TIMEOUT",
                    message="Request to Bodhan Indic-Speak timed out.",
                    retryable=True,
                )
            except Exception as e:
                if call_id:
                    update_spend_ledger(
                        call_id=call_id,
                        status="failed",
                        error_code="NETWORK_ERROR",
                        ledger_path=self.ledger_path,
                    )
                return None, StructuredError(
                    code="NETWORK_ERROR",
                    message=f"Network error during Indic-Speak call: {str(e)}",
                    retryable=False,
                )

        if response_2xx is None:
            return None, last_err or StructuredError(
                code="REQUEST_FAILED", message="Speech request failed."
            )

        # Once a 2xx response was received, NEVER retry!
        # If any reading/audio decoding exception occurs here, return StructuredError with UNKNOWN_BILLED
        try:
            if call_id:
                update_spend_ledger(
                    call_id=call_id,
                    status="completed",
                    http_status=response_2xx.status_code,
                    billed_units=len(chunk_text),
                    actual_cost_inr=est_cost,
                    ledger_path=self.ledger_path,
                )

            wav_bytes = await response_2xx.aread()
            if not wav_bytes:
                return None, StructuredError(
                    code="EMPTY_AUDIO",
                    message="Bodhan Indic-Speak returned empty audio stream.",
                    retryable=False,
                )

            # Detect audio format from magic bytes (never trust Content-Type header)
            audio_fmt = detect_audio_format(wav_bytes)
            if audio_fmt == "unknown":
                try:
                    # Use response's own decoding (response_2xx.text), not manual byte decoding
                    resp_text = response_2xx.text
                    if "\ufffd" in resp_text:
                        return None, StructuredError(
                            code="UNICODE_REPLACEMENT_CHAR",
                            message="Response contains invalid Unicode replacement character (U+FFFD).",
                            retryable=False,
                        )
                    err_data = json.loads(resp_text)
                    msg = err_data.get("error", {}).get("message", str(err_data))
                    return None, StructuredError(
                        code="INVALID_AUDIO_FORMAT",
                        message=f"Received non-audio response from Indic-Speak: {msg}",
                        retryable=False,
                    )
                except Exception:
                    pass

            return wav_bytes, None

        except Exception as e:
            return None, StructuredError(
                code="UNKNOWN_BILLED",
                message=f"Request succeeded with HTTP {response_2xx.status_code} and was billed, but response processing failed: {str(e)}",
                retryable=False,
            )

    async def speak(
        self,
        input_text: Optional[str] = None,
        voice: Optional[str] = None,
        language: str = "hi",
        output_path: str | Path = "output.wav",
        max_cost_inr: Optional[float] = None,
        text: Optional[str] = None,
    ) -> SpeakOutput:
        """Synthesizes input text to WAV audio with chunking, verification, and caching."""
        dest_path = Path(output_path).resolve()
        budget = max_cost_inr if max_cost_inr is not None else self.max_cost_inr

        raw_input = input_text or text or ""

        # 1. Resolve text from string or .txt file
        clean_text, err = self._resolve_text(raw_input)
        if err or not clean_text:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                error=err or StructuredError(code="EMPTY_INPUT", message="Input text is empty."),
            )

        char_count = len(clean_text)
        if "\ufffd" in clean_text:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                character_count=char_count,
                error=StructuredError(
                    code="UNICODE_REPLACEMENT_CHAR",
                    message="Input text contains invalid Unicode replacement character (U+FFFD).",
                    retryable=False,
                ),
            )

        # 2. Validate language against supported 23 codes BEFORE any HTTP call
        raw_lang = language.strip().lower()
        lang_name_map = {
            "hindi": "hi",
            "english": "en",
            "assamese": "as",
            "bengali": "bn",
            "bodo": "brx",
            "dogri": "doi",
            "gujarati": "gu",
            "kannada": "kn",
            "kashmiri": "ks",
            "konkani": "kok",
            "maithili": "mai",
            "malayalam": "ml",
            "manipuri": "mni",
            "marathi": "mr",
            "nepali": "ne",
            "odia": "or",
            "punjabi": "pa",
            "sanskrit": "sa",
            "santali": "sat",
            "sindhi": "sd",
            "tamil": "ta",
            "telugu": "te",
            "urdu": "ur",
        }
        lang_code = lang_name_map.get(raw_lang, raw_lang)
        if lang_code not in ALL_SUPPORTED_LANGUAGES:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                character_count=char_count,
                error=StructuredError(
                    code="UNSUPPORTED_LANGUAGE",
                    message=f"Unsupported language code '{language}'. Supported codes: {sorted(list(ALL_SUPPORTED_LANGUAGES))}",
                    retryable=False,
                ),
            )

        # 3. Validate voice against recognized catalog BEFORE any HTTP call
        raw_voice = str(voice).strip() if voice else ""
        if not raw_voice:
            chosen_voice = DEFAULT_FEMALE_VOICES.get(lang_code, "Kavya")
        elif raw_voice.lower() == "male":
            if lang_code not in DEFAULT_MALE_VOICES or not DEFAULT_MALE_VOICES[lang_code]:
                return SpeakOutput(
                    success=False,
                    output_file_path=str(output_path),
                    character_count=char_count,
                    error=StructuredError(
                        code="NO_MALE_VOICE",
                        message=f"Language '{lang_code}' has no documented male voice in the Bodhan catalog. Supported voices for '{lang_code}': {VOICES_BY_LANGUAGE.get(lang_code, [])}",
                        retryable=False,
                    ),
                )
            chosen_voice = DEFAULT_MALE_VOICES[lang_code]
        elif raw_voice.lower() == "female":
            if lang_code not in DEFAULT_FEMALE_VOICES or not DEFAULT_FEMALE_VOICES[lang_code]:
                return SpeakOutput(
                    success=False,
                    output_file_path=str(output_path),
                    character_count=char_count,
                    error=StructuredError(
                        code="NO_FEMALE_VOICE",
                        message=f"Language '{lang_code}' has no documented female voice in the Bodhan catalog. Supported voices for '{lang_code}': {VOICES_BY_LANGUAGE.get(lang_code, [])}",
                        retryable=False,
                    ),
                )
            chosen_voice = DEFAULT_FEMALE_VOICES[lang_code]
        else:
            chosen_voice = raw_voice

        if chosen_voice not in ALL_VALID_VOICES:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                character_count=char_count,
                error=StructuredError(
                    code="INVALID_VOICE",
                    message=f"Voice '{voice}' is not recognized. Valid voices: {sorted(list(ALL_VALID_VOICES))}",
                    retryable=False,
                ),
            )

        # 4. Check Preview tier warning
        preview_warning = None
        if lang_code in PREVIEW_LANGUAGES:
            preview_warning = f"Language '{lang_code}' is in Preview tier; voice stability and pronunciation pacing may vary."

        # 5. Pre-flight budget check BEFORE any HTTP call
        est_cost = calculate_speak_cost_inr(char_count)
        if est_cost > budget:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                character_count=char_count,
                estimated_cost_inr=est_cost,
                warning=preview_warning,
                error=StructuredError(
                    code="BUDGET_EXCEEDED",
                    message=f"Estimated cost (Rs {est_cost:.4f}) exceeds call budget ceiling (Rs {budget:.2f}).",
                    retryable=False,
                ),
            )

        # 6. Check system binaries for audio handling
        if not self.ffmpeg_bin or not self.ffprobe_bin:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                character_count=char_count,
                warning=preview_warning,
                error=StructuredError(
                    code="FFMPEG_MISSING",
                    message="ffmpeg or ffprobe executable is not installed or not found on system PATH.",
                    retryable=False,
                ),
            )

        # 7. Check API key
        if not self.api_key:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                character_count=char_count,
                warning=preview_warning,
                error=StructuredError(
                    code="AUTH_ERROR",
                    message="BODHAN_SPEAK_KEY is missing from environment.",
                    retryable=False,
                ),
            )

        # 8. Chunk by sentence at MAX 250 characters
        chunks = chunk_text_by_sentence(clean_text, max_chars=MAX_CHUNK_CHARS)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        call_tracker = CallTracker(max_calls=len(chunks) * 2 + 2)
        chunk_results: List[SpeakChunkResult] = []
        chunk_wav_files: List[Path] = []
        cached_count = 0
        billed_count = 0

        async def run_pipeline(client: httpx.AsyncClient):
            nonlocal cached_count, billed_count
            for idx, c_text in enumerate(chunks, 1):
                cache_key = get_cache_key("indic-speak", chosen_voice, lang_code, c_text)
                cache_file = self.cache_dir / f"{cache_key}.wav"

                # Check disk cache: never cache an error or empty audio
                if cache_file.exists() and cache_file.stat().st_size > 0:
                    if (
                        not is_mock_transport(self.custom_client)
                        or os.getenv("BODHAN_RECORD_MOCK_SPEND") == "1"
                    ):
                        log_spend_cache_hit(
                            model=MODEL_NAME,
                            caller="indic_speak",
                            run_id=self.run_id,
                            ledger_path=self.ledger_path,
                        )
                    chunk_results.append(
                        SpeakChunkResult(
                            chunk_index=idx,
                            text=c_text,
                            characters=len(c_text),
                            cache_hit=True,
                        )
                    )
                    cached_count += 1
                    chunk_wav_files.append(cache_file)
                    continue

                wav_bytes, err = await self._speak_single_chunk(
                    client, c_text, chosen_voice, lang_code, call_tracker
                )
                if err or not wav_bytes:
                    chunk_results.append(
                        SpeakChunkResult(
                            chunk_index=idx,
                            text=c_text,
                            characters=len(c_text),
                            cache_hit=False,
                            error=err
                            or StructuredError(code="EMPTY_AUDIO", message="Empty audio."),
                        )
                    )
                    return err

                chunk_results.append(
                    SpeakChunkResult(
                        chunk_index=idx,
                        text=c_text,
                        characters=len(c_text),
                        cache_hit=False,
                    )
                )
                billed_count += 1

                # Write to disk cache
                cache_file.write_bytes(wav_bytes)
                chunk_wav_files.append(cache_file)
            return None

        if self.custom_client:
            exec_err = await run_pipeline(self.custom_client)
        else:
            async with httpx.AsyncClient() as client:
                exec_err = await run_pipeline(client)

        if exec_err:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                character_count=char_count,
                chunks_count=len(chunks),
                cached_chunks_count=cached_count,
                billed_chunks_count=billed_count,
                warning=preview_warning,
                chunks=chunk_results,
                error=exec_err,
            )

        # 9. Concatenate WAV chunks via ffmpeg
        try:
            concatenate_wav_chunks(chunk_wav_files, dest_path, self.ffmpeg_bin)
        except Exception as e:
            return SpeakOutput(
                success=False,
                output_file_path=str(output_path),
                character_count=char_count,
                chunks_count=len(chunks),
                cached_chunks_count=cached_count,
                billed_chunks_count=billed_count,
                warning=preview_warning,
                chunks=chunk_results,
                error=StructuredError(
                    code="AUDIO_CONCAT_ERROR",
                    message=f"Failed to concatenate audio chunks: {str(e)}",
                    retryable=False,
                ),
            )

        # 10. Local verification: ffprobe duration > 0 and 4 to 25 chars/sec check
        verified, ver_reason, duration_s = verify_speech_audio(
            dest_path, char_count, self.ffprobe_bin
        )

        billed_chars = sum(c.characters for c in chunk_results if not c.cache_hit)
        actual_cost = calculate_speak_cost_inr(billed_chars)

        return SpeakOutput(
            success=True,
            output_file_path=str(dest_path),
            character_count=char_count,
            chunks_count=len(chunks),
            cached_chunks_count=cached_count,
            billed_chunks_count=billed_count,
            duration_seconds=round(duration_s, 3),
            verified=verified,
            verification_reason=ver_reason,
            warning=preview_warning,
            estimated_cost_inr=actual_cost,
            chunks=chunk_results,
        )
