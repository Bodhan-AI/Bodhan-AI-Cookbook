"""Normalize local audio and send bounded WAV windows to Indic-Transcribe."""

import io
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Annotated, Iterator, Literal
import wave

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

TRANSCRIBE_URL = "https://api.bodhan.ai/v1/audio/transcriptions"
SAMPLE_RATE = 16_000
MAX_WINDOW_FRAMES = 30 * SAMPLE_RATE

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


def _wav_windows(path: Path) -> Iterator[tuple[float, float, bytes]]:
    with wave.open(str(path), "rb") as source:
        if not source.getnframes():
            raise ToolError("empty_audio: Provide a recording with at least one sample.")
        while source.tell() < source.getnframes():
            start_frame = source.tell()
            frames = source.readframes(MAX_WINDOW_FRAMES)
            if not frames:
                raise ToolError("invalid_audio: The decoded WAV ended unexpectedly.")
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
    try:
        reply = client.post(
            TRANSCRIBE_URL,
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
            429: "rate_limited: Wait before retrying.",
        }
        raise ToolError(errors.get(reply.status_code, f"upstream_error: Bodhan returned HTTP {reply.status_code}."))
    try:
        return _TranscriptionResponse.model_validate_json(reply.content, strict=True).text.strip()
    except ValidationError:
        raise ToolError("invalid_response: Bodhan returned an unexpected transcription response.") from None


def transcribe_audio(
    audio_path: Annotated[str, Field(min_length=1, description="Path to a local audio file.")],
    language: Annotated[SpeechLanguage | None, Field(description="Known spoken language code, such as hi.")] = None,
) -> TranscriptionResult:
    """Transcribe local audio using Indic-Transcribe.

    Pass a file path. The tool splits recordings into windows of up to 30 seconds.
    Window times are audio boundaries. Omit language to let Bodhan infer it.
    """
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
        for start, end, audio in _wav_windows(wav):
            try:
                text = _transcribe_window(client, audio, language, api_key)
            except ToolError as exc:
                raise ToolError(f"Window {start:g}–{end:g} seconds: {exc}") from None
            windows.append(TranscriptionWindow(start_seconds=start, end_seconds=end, text=text))

    return TranscriptionResult(
        text=" ".join(window.text for window in windows if window.text),
        duration_seconds=windows[-1].end_seconds,
        windows=windows,
    )
