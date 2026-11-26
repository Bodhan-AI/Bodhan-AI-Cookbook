"""Normalize local audio and send bounded WAV windows to Indic-Transcribe."""

import io
import os
import subprocess
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from struct import iter_unpack
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from .api_requests import post_bodhan
from .request_budget import TRANSCRIBE_PER_MINUTE, BudgetExhausted

TRANSCRIBE_URL = "https://api.bodhan.ai/v1/audio/transcriptions"
SAMPLE_RATE = 16_000
MAX_WINDOW_FRAMES = 30 * SAMPLE_RATE
PAUSE_BLOCK_FRAMES = SAMPLE_RATE // 50
PAUSE_SEARCH_FRAMES = 2 * SAMPLE_RATE
MIN_PAUSE_BLOCKS = 6
QUIET_RMS = 250

SpeechLanguage = Literal[
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
]


class TranscriptionWindow(BaseModel):
    start_seconds: float
    end_seconds: float
    text: str


class TranscriptionResult(BaseModel):
    text: str
    duration_seconds: float
    windows: list[TranscriptionWindow]


class _TranscriptionResponse(BaseModel):
    text: str


def _normalize(source: Path, destination: Path) -> None:
    try:
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
                "-i", str(source), "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s16le", str(destination),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
        )
    except FileNotFoundError:
        raise ToolError("missing_ffmpeg: Install ffmpeg and make it available on PATH.") from None
    except subprocess.TimeoutExpired:
        raise ToolError("audio_decode_timeout: Try a shorter recording.") from None
    except subprocess.CalledProcessError:
        raise ToolError("invalid_audio: Provide a readable file with an audio stream.") from None


def _pause_boundary(pcm: bytes) -> int:
    """Find the latest pause near the end of a normalized PCM window."""
    end = len(pcm) // 2
    search_start = max(end - PAUSE_SEARCH_FRAMES, end // 2)
    block_frames = PAUSE_BLOCK_FRAMES
    quiet_blocks = 0
    pause_start = end
    pause_end = end
    for start in range(end - block_frames, search_start - 1, -block_frames):
        block = pcm[start * 2:(start + block_frames) * 2]
        energy = sum(sample * sample for (sample,) in iter_unpack("<h", block))
        if energy <= block_frames * QUIET_RMS**2:
            if quiet_blocks == 0:
                pause_end = start + block_frames
            pause_start = start
            quiet_blocks += 1
            continue
        if quiet_blocks >= MIN_PAUSE_BLOCKS:
            return (pause_start + pause_end) // 2 if pause_end < end else end
        quiet_blocks = 0
    if quiet_blocks >= MIN_PAUSE_BLOCKS and pause_end < end:
        return (pause_start + pause_end) // 2
    return end


def _wav_windows(
    path: Path, window_frames: int = MAX_WINDOW_FRAMES, *, prefer_pauses: bool = False,
) -> Iterator[tuple[float, float, bytes]]:
    if not 0 < window_frames <= MAX_WINDOW_FRAMES:
        raise ToolError("invalid_window: Choose a window of at least one sample and at most 30 seconds.")
    with wave.open(str(path), "rb") as source:
        if not source.getnframes():
            raise ToolError("empty_audio: Provide a recording with at least one sample.")
        while source.tell() < source.getnframes():
            start_frame = source.tell()
            frames = source.readframes(window_frames)
            if not frames:
                raise ToolError("invalid_audio: The decoded WAV ended unexpectedly.")
            if prefer_pauses and source.tell() < source.getnframes():
                kept_frames = _pause_boundary(frames)
                frames = frames[:kept_frames * 2]
                source.setpos(start_frame + kept_frames)
            output = io.BytesIO()
            with wave.open(output, "wb") as window:
                window.setparams(source.getparams())
                window.writeframes(frames)
            yield start_frame / SAMPLE_RATE, source.tell() / SAMPLE_RATE, output.getvalue()


def _transcribe_window(
    client: httpx.Client, audio: bytes, language: SpeechLanguage | None, api_key: str
) -> str:
    data = {"model": "indic-transcribe"}
    if language is not None:
        data["language"] = language
    with wave.open(io.BytesIO(audio), "rb") as window:
        # Apply the rate before dividing to keep exact prices at budget boundaries.
        cost = window.getnframes() * TRANSCRIBE_PER_MINUTE / (window.getframerate() * 60)
    try:
        reply, _ = post_bodhan(
            client, TRANSCRIBE_URL, "indic-transcribe", cost,
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": ("window.wav", audio, "audio/wav")},
        )
    except httpx.TimeoutException:
        raise ToolError("timeout: Bodhan did not respond in time.") from None
    except httpx.RequestError:
        raise ToolError("connection_error: Could not reach the Bodhan transcription API.") from None

    if reply.status_code != 200:
        errors = {
            400: "invalid_request: Check the audio and language.",
            401: "authentication_failed: Check the Bodhan transcription key.",
            403: "access_denied: Check that the key has transcription access.",
            415: "unsupported_audio: Bodhan could not read the WAV window.",
        }
        raise ToolError(errors.get(reply.status_code, f"upstream_error: Bodhan returned HTTP {reply.status_code}."))
    try:
        return _TranscriptionResponse.model_validate_json(reply.content, strict=True).text.strip()
    except ValidationError:
        raise ToolError("invalid_response: Bodhan returned an unexpected transcription response.") from None


def transcribe_audio(
    audio_path: Annotated[str, Field(min_length=1, description="Path to a local audio file.")],
    language: Annotated[SpeechLanguage | None, Field(description="Known spoken language code, such as hi.")] = None,
    window_seconds: Annotated[int, Field(ge=1, le=30, description="Maximum seconds of audio per request.")] = 30,
    prefer_pauses: Annotated[bool, Field(description="Prefer nearby quiet audio when placing window boundaries.")] = False,
) -> TranscriptionResult:
    """Transcribe local audio using Indic-Transcribe.

    Pass a file path. The tool splits recordings into windows of up to 30 seconds.
    Set window_seconds to get shorter windows for subtitles and prefer_pauses
    to favor quiet cuts. Window times are audio boundaries. Omit language to
    let Bodhan infer it.
    """
    return read_audio(audio_path, language, window_seconds, prefer_pauses=prefer_pauses)


def read_audio(
    audio_path: str, language: SpeechLanguage | None = None, window_seconds: int = 30,
    *, prefer_pauses: bool = False, on_window: Callable[[TranscriptionWindow], None] | None = None,
) -> TranscriptionResult:
    source = Path(audio_path).expanduser().absolute()
    if not source.is_file():
        raise ToolError("missing_audio: Provide a path to an existing audio file.")
    api_key = os.getenv("BODHAN_TRANSCRIBE_KEY", "").strip()
    if not api_key:
        raise ToolError("missing_key: Set BODHAN_TRANSCRIBE_KEY in the recipe's .env file.")

    windows: list[TranscriptionWindow] = []
    with TemporaryDirectory(prefix="bodhan-audio-") as directory, httpx.Client(timeout=30.0) as client:
        wav = Path(directory) / "audio.wav"
        _normalize(source, wav)
        for start, end, audio in _wav_windows(wav, window_seconds * SAMPLE_RATE, prefer_pauses=prefer_pauses):
            try:
                text = _transcribe_window(client, audio, language, api_key)
            except (BudgetExhausted, ToolError) as exc:
                raise type(exc)(f"Window {start:g}–{end:g} seconds: {exc}") from None
            window = TranscriptionWindow(start_seconds=start, end_seconds=end, text=text)
            windows.append(window)
            if on_window is not None:
                on_window(window)

    return TranscriptionResult(
        text=" ".join(window.text for window in windows if window.text),
        duration_seconds=windows[-1].end_seconds,
        windows=windows,
    )
