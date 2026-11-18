"""Indic-Transcribe tool for Bodhan MCP Server.

Wraps Bodhan AI's Indic-Transcribe model (POST https://api.bodhan.ai/v1/audio/transcriptions)
with ffprobe duration verification, 30-second audio windowing with silence-based
splitting, per-chunk local disk caching, pre-flight cost estimation, and structured
error handling for HTTP 415, 429, and 5xx.
"""

from __future__ import annotations

import asyncio
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
    calculate_transcribe_cost_inr,
    classify_http_response,
    get_cache_key,
    read_cache,
    write_cache,
    check_live_calls_allowed,
    check_global_cap_allowed,
    is_mock_transport,
    log_spend_dispatched,
    update_spend_ledger,
    log_spend_cache_hit,
)
from bodhan_mcp.tools.translate import normalize_language

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------

MODEL_NAME = "indic-transcribe"
CACHE_DIR = Path(".cache/bodhan_transcribe")
MAX_WINDOW_SECONDS = 30.0
SPLIT_WINDOW_MIN_SECONDS = 20.0
SPLIT_WINDOW_MAX_SECONDS = 29.0
HARD_CUT_SECONDS = 29.0
SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


# ------------------------------------------------------------------------------
# System Tool Helpers (ffmpeg / ffprobe)
# ------------------------------------------------------------------------------


def get_ffmpeg_binaries() -> Tuple[Optional[str], Optional[str]]:
    """Locates ffmpeg and ffprobe executables on PATH or user directory."""
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")

    if ffmpeg_bin and ffprobe_bin:
        return ffmpeg_bin, ffprobe_bin

    # Fallback to standard Windows user Programs location if not yet inherited
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        user_bin = Path(local_app_data) / "Programs" / "ffmpeg" / "bin"
        if not ffmpeg_bin and (user_bin / "ffmpeg.exe").exists():
            ffmpeg_bin = str(user_bin / "ffmpeg.exe")
        if not ffprobe_bin and (user_bin / "ffprobe.exe").exists():
            ffprobe_bin = str(user_bin / "ffprobe.exe")

    return ffmpeg_bin, ffprobe_bin


def probe_audio_duration(
    file_path: Path, ffprobe_bin: str
) -> Tuple[Optional[float], Optional[StructuredError]]:
    """Reads audio duration in seconds using ffprobe."""
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=15)
        if proc.returncode != 0:
            return None, StructuredError(
                code="UNSUPPORTED_MEDIA_TYPE",
                message=f"ffprobe failed to parse audio file (HTTP 415): {proc.stderr.strip()}",
                retryable=False,
            )
        output = proc.stdout.strip()
        if not output:
            return None, StructuredError(
                code="UNSUPPORTED_MEDIA_TYPE",
                message="ffprobe returned empty duration for audio file.",
                retryable=False,
            )
        duration = float(output)
        return duration, None
    except FileNotFoundError:
        return None, StructuredError(
            code="FFMPEG_MISSING",
            message=f"ffprobe executable not found at '{ffprobe_bin}'.",
            retryable=False,
        )
    except Exception as e:
        return None, StructuredError(
            code="UNSUPPORTED_MEDIA_TYPE",
            message=f"Error reading audio duration: {str(e)}",
            retryable=False,
        )


def detect_silence_intervals(
    audio_path: Path, ffmpeg_bin: str, noise_thresh_db: int = -30, min_duration: float = 0.2
) -> List[Tuple[float, float]]:
    """Runs ffmpeg silencedetect filter and parses (start, end) timestamps of silences."""
    cmd = [
        ffmpeg_bin,
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={noise_thresh_db}dB:d={min_duration}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
        stderr = proc.stderr
        silences: List[Tuple[float, float]] = []
        starts = re.findall(r"silence_start:\s*([\d\.]+)", stderr)
        ends = re.findall(r"silence_end:\s*([\d\.]+)", stderr)
        for s, e in zip(starts, ends):
            try:
                silences.append((float(s), float(e)))
            except ValueError:
                continue
        return silences
    except Exception:
        return []


def compute_chunk_windows(
    total_duration: float, silences: List[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    """Computes audio chunk windows (each <= 30.0s).

    Splits at silence between 20.0s and 29.0s from current chunk start.
    If no silence found, hard-cuts at 29.0s. Never cuts mid-syllable when silence exists.
    """
    if total_duration <= MAX_WINDOW_SECONDS:
        return [(0.0, round(total_duration, 3))]

    chunks: List[Tuple[float, float]] = []
    current_start = 0.0

    while current_start < total_duration:
        remaining = total_duration - current_start
        if remaining <= MAX_WINDOW_SECONDS:
            chunks.append((round(current_start, 3), round(total_duration, 3)))
            break

        window_min = current_start + SPLIT_WINDOW_MIN_SECONDS
        window_max = current_start + SPLIT_WINDOW_MAX_SECONDS

        # Look for silence in [window_min, window_max]
        candidate_cut = None
        for s_start, s_end in reversed(silences):
            mid = (s_start + s_end) / 2.0
            if window_min <= mid <= window_max:
                candidate_cut = mid
                break
            if s_start <= window_max and s_end >= window_min:
                clamped = max(window_min, min(window_max, (s_start + s_end) / 2.0))
                candidate_cut = clamped
                break

        split_point = (
            candidate_cut if candidate_cut is not None else (current_start + HARD_CUT_SECONDS)
        )
        chunks.append((round(current_start, 3), round(split_point, 3)))
        current_start = split_point

    return chunks


def extract_chunk_wav_bytes(
    audio_path: Path, ffmpeg_bin: str, start_s: float, end_s: float
) -> bytes:
    """Extracts an audio slice into 16kHz mono 16-bit PCM WAV bytes."""
    cmd = [
        ffmpeg_bin,
        "-v",
        "error",
        "-ss",
        str(start_s),
        "-to",
        str(end_s),
        "-i",
        str(audio_path),
        "-c:a",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "wav",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True, timeout=30)
    return proc.stdout


# ------------------------------------------------------------------------------
# Pydantic Schemas
# ------------------------------------------------------------------------------


class TranscribeChunkResult(BaseModel):
    chunk_index: int = Field(..., description="1-indexed chunk order.")
    start_second: float = Field(..., description="Start timestamp of the chunk in seconds.")
    end_second: float = Field(..., description="End timestamp of the chunk in seconds.")
    text: str = Field("", description="Transcribed text for this chunk.")
    cache_hit: bool = Field(
        False, description="Whether this chunk was served from local disk cache."
    )
    error: Optional[StructuredError] = Field(None, description="Structured error if chunk failed.")


class TranscribeInput(BaseModel):
    file_path: str = Field(..., description="Path to audio file (WAV, MP3, FLAC, OGG).")
    language: Optional[str] = Field(
        None,
        description="Optional ISO language code (e.g. 'hi', 'kn', 'ta', 'en'). Omit for auto-detect.",
    )


class TranscribeOutput(BaseModel):
    success: bool
    file_path: str
    duration_seconds: float = 0.0
    language: Optional[str] = None
    language_source: str = "request"
    chunks_count: int = 0
    cached_chunks_count: int = 0
    billed_chunks_count: int = 0
    chunks: List[TranscribeChunkResult] = []
    text: str = ""
    estimated_cost_inr: float = 0.0
    error: Optional[StructuredError] = None


# ------------------------------------------------------------------------------
# Tool Implementation
# ------------------------------------------------------------------------------


class IndicTranscribeTool:
    """Manages audio transcription calls to Bodhan AI's indic-transcribe model."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BODHAN_API_BASE,
        cache_dir: Path = CACHE_DIR,
        client: Optional[httpx.AsyncClient] = None,
        max_concurrency: int = 2,
        ffmpeg_bin: Optional[str] = None,
        ffprobe_bin: Optional[str] = None,
        run_id: Optional[str] = None,
        ledger_path: Optional[Union[str, Path]] = None,
    ):
        self.api_key = api_key or os.getenv("BODHAN_TRANSCRIBE_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.custom_client = client
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.run_id = run_id or os.getenv("BODHAN_RUN_ID", "default")
        self.ledger_path = ledger_path

        # Binary discovery
        disc_ffmpeg, disc_ffprobe = get_ffmpeg_binaries()
        self.ffmpeg_bin = ffmpeg_bin or disc_ffmpeg
        self.ffprobe_bin = ffprobe_bin or disc_ffprobe

    def estimate_cost(self, file_path: str | Path) -> Dict[str, Any]:
        """Pre-flight cost estimation based on audio duration without calling the API."""
        path_obj = Path(file_path).resolve()

        if not path_obj.exists():
            return {
                "file_path": str(file_path),
                "error": f"File not found: {file_path}",
                "duration_seconds": 0.0,
                "estimated_cost_inr": 0.0,
            }

        ffmpeg_valid = bool(
            self.ffmpeg_bin and (shutil.which(self.ffmpeg_bin) or Path(self.ffmpeg_bin).exists())
        )
        ffprobe_valid = bool(
            self.ffprobe_bin and (shutil.which(self.ffprobe_bin) or Path(self.ffprobe_bin).exists())
        )

        if not ffmpeg_valid or not ffprobe_valid:
            return {
                "file_path": str(file_path),
                "error": "ffmpeg or ffprobe binary missing from system.",
                "duration_seconds": 0.0,
                "estimated_cost_inr": 0.0,
            }

        duration, err = probe_audio_duration(path_obj, self.ffprobe_bin)
        if err or duration is None:
            return {
                "file_path": str(file_path),
                "error": err.message if err else "Could not read audio duration.",
                "duration_seconds": 0.0,
                "estimated_cost_inr": 0.0,
            }

        pro_rata_cost = calculate_transcribe_cost_inr(duration)
        expected_chunks = max(1, int(duration // 29.0) + (1 if duration % 29.0 > 0 else 0))
        worst_case_cost = round(expected_chunks * 0.10, 4)

        return {
            "file_path": str(file_path),
            "duration_seconds": round(duration, 3),
            "estimated_chunks": expected_chunks,
            "cost_pro_rata_inr": pro_rata_cost,
            "cost_worst_case_inr": worst_case_cost,
            "estimated_cost_inr": pro_rata_cost,
        }

    async def _transcribe_single_chunk(
        self,
        client: httpx.AsyncClient,
        chunk_bytes: bytes,
        language: Optional[str],
        call_tracker: CallTracker,
        chunk_duration: float = 30.0,
    ) -> Tuple[Optional[str], Optional[StructuredError]]:
        """Sends a single <=30s audio chunk to POST /v1/audio/transcriptions."""
        url = f"{self.base_url}/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        # Build multipart/form-data payload
        data_fields: Dict[str, Any] = {"model": MODEL_NAME}
        if language:
            data_fields["language"] = language

        files = {
            "file": ("chunk.wav", chunk_bytes, "audio/wav"),
        }

        # Live-call safety guard: block real HTTP unless BODHAN_ALLOW_LIVE=1
        guard_err = check_live_calls_allowed(url, client)
        if guard_err:
            return None, guard_err

        # Global spend cap guard
        est_cost = calculate_transcribe_cost_inr(chunk_duration)
        cap_err = check_global_cap_allowed(est_cost, self.ledger_path)
        if cap_err:
            return None, cap_err

        max_retries = 2
        response_2xx: Optional[httpx.Response] = None
        last_err: Optional[StructuredError] = None
        call_id: Optional[str] = None
        fffd_retry_count = 0

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
                    caller="indic_transcribe",
                    run_id=self.run_id,
                    ledger_path=self.ledger_path,
                )

            try:
                async with self.semaphore:
                    response = await client.post(
                        url, headers=headers, data=data_fields, files=files, timeout=60.0
                    )

                # Check if 2xx response was received
                if 200 <= response.status_code < 300:
                    # Check for U+FFFD using response's own decoding (response.json())
                    try:
                        parsed_data = response.json()
                        text_content = parsed_data.get("text", "")
                        if "\ufffd" in text_content:
                            if fffd_retry_count < 1 and attempt < max_retries:
                                fffd_retry_count += 1
                                await asyncio.sleep(0.5)
                                continue
                            return None, StructuredError(
                                code="UNICODE_REPLACEMENT_CHAR",
                                message="Transcription output contains invalid Unicode replacement character (U+FFFD).",
                                retryable=False,
                            )
                        response_2xx = response
                        break
                    except Exception:
                        response_2xx = response
                        break

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
                    message="Request to Bodhan Indic-Transcribe timed out.",
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
                    message=f"Network error during Indic-Transcribe call: {str(e)}",
                    retryable=False,
                )

        if response_2xx is None:
            return None, last_err or StructuredError(
                code="REQUEST_FAILED", message="Transcription request failed."
            )

        # Once a 2xx response was received, NEVER retry!
        # If any parsing exception occurs here, return StructuredError with UNKNOWN_BILLED
        try:
            if call_id:
                update_spend_ledger(
                    call_id=call_id,
                    status="completed",
                    http_status=response_2xx.status_code,
                    billed_units=chunk_duration,
                    actual_cost_inr=est_cost,
                    ledger_path=self.ledger_path,
                )

            data = response_2xx.json()
            text = data.get("text", "")
            return text, None

        except Exception as e:
            return None, StructuredError(
                code="UNKNOWN_BILLED",
                message=f"Request succeeded with HTTP {response_2xx.status_code} and was billed, but response processing failed: {str(e)}",
                retryable=False,
            )

    async def transcribe(
        self,
        file_path: str | Path,
        language: Optional[str] = None,
    ) -> TranscribeOutput:
        """Transcribes an audio file into text across 30-second windows with caching."""
        path_obj = Path(file_path).resolve()

        # 0. Language normalization to canonical ISO code (e.g. 'bengali' -> 'bn')
        canon_code, _ = normalize_language(language)
        effective_lang = (
            canon_code if canon_code else (language.strip().lower() if language else None)
        )

        # 1. Pre-flight file validation
        if not path_obj.exists():
            return TranscribeOutput(
                success=False,
                file_path=str(file_path),
                error=StructuredError(
                    code="FILE_NOT_FOUND",
                    message=f"File not found: {file_path}",
                    retryable=False,
                ),
            )

        if not path_obj.is_file():
            return TranscribeOutput(
                success=False,
                file_path=str(file_path),
                error=StructuredError(
                    code="NOT_A_FILE",
                    message=f"Path is not a regular file: {file_path}",
                    retryable=False,
                ),
            )

        # 2. Check ffmpeg and ffprobe presence
        ffmpeg_valid = bool(
            self.ffmpeg_bin and (shutil.which(self.ffmpeg_bin) or Path(self.ffmpeg_bin).exists())
        )
        ffprobe_valid = bool(
            self.ffprobe_bin and (shutil.which(self.ffprobe_bin) or Path(self.ffprobe_bin).exists())
        )

        if not ffmpeg_valid or not ffprobe_valid:
            return TranscribeOutput(
                success=False,
                file_path=str(file_path),
                error=StructuredError(
                    code="FFMPEG_MISSING",
                    message="ffmpeg or ffprobe executable is not installed or not found on system PATH.",
                    retryable=False,
                ),
            )

        # 3. Read duration via ffprobe
        total_duration, probe_err = probe_audio_duration(path_obj, self.ffprobe_bin)
        if probe_err or total_duration is None:
            return TranscribeOutput(
                success=False,
                file_path=str(file_path),
                error=probe_err
                or StructuredError(code="AUDIO_PROBE_ERROR", message="Failed to read duration."),
            )

        # 4. Check API key
        if not self.api_key:
            return TranscribeOutput(
                success=False,
                file_path=str(file_path),
                duration_seconds=total_duration,
                error=StructuredError(
                    code="AUTH_ERROR",
                    message="BODHAN_TRANSCRIBE_KEY is missing from environment.",
                    retryable=False,
                ),
            )

        # 5. Silence detection and chunk calculation
        silences = detect_silence_intervals(path_obj, self.ffmpeg_bin)
        windows = compute_chunk_windows(total_duration, silences)

        call_tracker = CallTracker(max_calls=len(windows) * 2 + 2)
        chunk_results: List[TranscribeChunkResult] = []
        cached_count = 0
        billed_count = 0

        async def run_pipeline(client: httpx.AsyncClient):
            nonlocal cached_count, billed_count
            for idx, (start_s, end_s) in enumerate(windows, 1):
                try:
                    chunk_bytes = extract_chunk_wav_bytes(path_obj, self.ffmpeg_bin, start_s, end_s)
                except Exception as e:
                    chunk_results.append(
                        TranscribeChunkResult(
                            chunk_index=idx,
                            start_second=start_s,
                            end_second=end_s,
                            text="",
                            cache_hit=False,
                            error=StructuredError(
                                code="AUDIO_EXTRACT_ERROR",
                                message=f"Failed to extract chunk {idx} [{start_s}-{end_s}s]: {str(e)}",
                                retryable=False,
                            ),
                        )
                    )
                    continue

                # Check cache: sha256(model + language + chunk_bytes)
                lang_tag = effective_lang or "auto"
                cache_key = get_cache_key(MODEL_NAME, lang_tag, chunk_bytes)
                cached = read_cache(cache_key, self.cache_dir)
                if cached is not None:
                    if (
                        not is_mock_transport(self.custom_client)
                        or os.getenv("BODHAN_RECORD_MOCK_SPEND") == "1"
                    ):
                        log_spend_cache_hit(
                            model=MODEL_NAME,
                            caller="indic_transcribe",
                            run_id=self.run_id,
                            ledger_path=self.ledger_path,
                        )
                    chunk_results.append(
                        TranscribeChunkResult(
                            chunk_index=idx,
                            start_second=start_s,
                            end_second=end_s,
                            text=cached.get("text", ""),
                            cache_hit=True,
                        )
                    )
                    cached_count += 1
                    continue

                # Call API
                text, err = await self._transcribe_single_chunk(
                    client,
                    chunk_bytes,
                    effective_lang,
                    call_tracker,
                    chunk_duration=max(0.1, end_s - start_s),
                )
                if err:
                    chunk_results.append(
                        TranscribeChunkResult(
                            chunk_index=idx,
                            start_second=start_s,
                            end_second=end_s,
                            text="",
                            cache_hit=False,
                            error=err,
                        )
                    )
                    continue

                chunk_results.append(
                    TranscribeChunkResult(
                        chunk_index=idx,
                        start_second=start_s,
                        end_second=end_s,
                        text=text or "",
                        cache_hit=False,
                    )
                )
                billed_count += 1

                # Save successful chunk to cache
                if "\ufffd" not in (text or ""):
                    write_cache(
                        cache_key,
                        {"text": text, "start_second": start_s, "end_second": end_s},
                        self.cache_dir,
                    )

        if self.custom_client:
            await run_pipeline(self.custom_client)
        else:
            async with httpx.AsyncClient() as client:
                await run_pipeline(client)

        successful_chunks = [c for c in chunk_results if c.error is None]
        first_error = next((c.error for c in chunk_results if c.error is not None), None)
        success = len(successful_chunks) > 0

        # Combine transcripts
        combined_text = " ".join(c.text.strip() for c in chunk_results if c.text).strip()
        total_cost = calculate_transcribe_cost_inr(total_duration) if billed_count > 0 else 0.0

        return TranscribeOutput(
            success=success,
            file_path=str(file_path),
            duration_seconds=total_duration,
            language=effective_lang,
            language_source="request",
            chunks_count=len(windows),
            cached_chunks_count=cached_count,
            billed_chunks_count=billed_count,
            chunks=chunk_results,
            text=combined_text,
            estimated_cost_inr=total_cost,
            error=first_error if not success else None,
        )
