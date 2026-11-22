"""Synthesize short passages on Bodhan and assemble a local audio artifact."""

import io
import json
import os
import re
import shutil
import subprocess
import textwrap
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from .api_requests import post_bodhan
from .document_chunks import text_for_speech
from .request_budget import SPEECH_RATE

SPEECH_URL = "https://api.bodhan.ai/v1/audio/speech"
# A conservative tool policy based on Bodhan's short-input guidance.
MAX_CHUNK_CHARACTERS = 200

SpeechLanguage = Literal[
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
]
Voice = Literal[
    "Prastuti", "Ankur", "Ishita", "Sourav", "Gwrbw", "Sansuma", "Preeti", "Sham",
    "Dhara", "Parth", "Kavya", "Suhani", "Amit", "Deepika", "Adarsh", "Anjali",
    "Sandeep", "Zoon", "Ishfaq", "Vaidehi", "Madhukar", "Lakshmi", "Kiran",
    "Thoibi", "Chaoba", "Anagha", "Chinmay", "Srijana", "Sagar", "Itishree",
    "Akash", "Kaur", "Manpreet", "Bharati", "Aryaman", "Phulmani", "Sibu",
    "Moomal", "Rano", "Anitha", "Arun", "Sravani", "Vamsi", "Saba", "Zaid",
]

# Voice recording languages and genders from the Bodhan API reference.
VOICE_CATALOG = {
    "as": ("Prastuti", "Ankur"),
    "bn": ("Ishita", "Sourav"),
    "brx": ("Gwrbw", "Sansuma"),
    "doi": ("Preeti", "Sham"),
    "gu": ("Dhara", "Parth"),
    "hi": ("Kavya, Suhani", "Amit"),
    "kn": ("Deepika", "Adarsh"),
    "kok": ("Anjali", "Sandeep"),
    "ks": ("Zoon", "Ishfaq"),
    "mai": ("Vaidehi", "Madhukar"),
    "ml": ("Lakshmi", "Kiran"),
    "mni": ("Thoibi", "Chaoba"),
    "mr": ("Anagha", "Chinmay"),
    "ne": ("Srijana", "Sagar"),
    "or": ("Itishree", "Akash"),
    "pa": ("Kaur", "Manpreet"),
    "sa": ("Bharati", "Aryaman"),
    "sat": ("Phulmani", "Sibu"),
    "sd": ("Moomal", "Rano"),
    "ta": ("Anitha", "Arun"),
    "te": ("Sravani", "Vamsi"),
    "ur": ("Saba", "Zaid"),
}


VOICE_DESCRIPTION = (
    "Choose a voice from Bodhan's library. Any voice can read any supported language. "
    "Recording profiles by language and gender. "
    + " | ".join(f"{code} (female {female}, male {male})"
                 for code, (female, male) in VOICE_CATALOG.items())
)


class SpeechResult(BaseModel):
    audio_path: str
    duration_seconds: float
    chunks: int
    ai_generated: Literal[True] = True


def _speech_chunks(text: str) -> list[str]:
    chunks = []
    for sentence in re.split(r"(?<=[.!?।॥])\s+|\n+", text.strip()):
        chunks.extend(textwrap.wrap(
            sentence, width=MAX_CHUNK_CHARACTERS,
            break_long_words=False, break_on_hyphens=False, expand_tabs=False,
        ))
    if not chunks:
        raise ToolError("empty_input: Provide text to read.")
    if any(len(chunk) > MAX_CHUNK_CHARACTERS for chunk in chunks):
        raise ToolError("oversized_word: Split the long unbroken span in the source text.")
    return chunks


def _request_speech(
    client: httpx.Client, chunk: str, language: SpeechLanguage, voice: Voice, key: str
) -> bytes:
    try:
        response, _ = post_bodhan(
            client, SPEECH_URL, "indic-speak", len(chunk) * SPEECH_RATE,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "indic-speak",
                "input": chunk,
                "voice": voice,
                "instructions": json.dumps({"lang": language}),
            },
        )
    except httpx.TimeoutException:
        raise ToolError("timeout: Bodhan did not respond in time.") from None
    except httpx.RequestError:
        raise ToolError("connection_error: Could not reach the Bodhan speech API.") from None
    if response.status_code != 200:
        errors = {
            401: "authentication_failed: Check the Bodhan speech key.",
            403: "access_denied: Check that the key has speech access.",
            422: "invalid_request: Check text, voice and language.",
        }
        raise ToolError(errors.get(response.status_code, f"upstream_error: Bodhan returned HTTP {response.status_code}."))
    return response.content


def _speech_pcm(wav_bytes: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 24_000):
                raise ToolError("invalid_audio: Expected 24 kHz mono PCM16 from Bodhan.")
            expected_frames = source.getnframes()
            frames = source.readframes(expected_frames)
    except (EOFError, wave.Error):
        raise ToolError("invalid_audio: Bodhan returned unreadable WAV audio.") from None
    if len(frames) != expected_frames * 2:
        raise ToolError("invalid_audio: Bodhan returned a truncated WAV chunk.")
    if not frames:
        raise ToolError("invalid_audio: Bodhan returned empty or malformed audio.")
    return frames


def _encode_audio(source: Path, destination: Path, tempo: float) -> None:
    codec = {"mp3": "libmp3lame", "wav": "pcm_s16le"}[destination.suffix[1:]]
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
             "-i", str(source), "-af", f"atempo={tempo:.6f}", "-c:a", codec,
             "-metadata", "comment=AI-generated with Bodhan Indic-Speak", str(destination)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
        )
    except FileNotFoundError:
        raise ToolError("missing_ffmpeg: Install ffmpeg and make it available on PATH.") from None
    except subprocess.TimeoutExpired:
        raise ToolError("audio_encode_timeout: Try a shorter passage.") from None
    except subprocess.CalledProcessError:
        raise ToolError("audio_encode_failed: Check that ffmpeg supports the requested format.") from None


def synthesize_speech(
    text: Annotated[str, Field(min_length=1, description="Text to read aloud in the requested language.")],
    language: Annotated[SpeechLanguage, Field(description="Language of the text, such as kn for Kannada.")],
    voice: Annotated[Voice, Field(description=VOICE_DESCRIPTION)],
    output_path: Annotated[str, Field(min_length=1, description="New local .wav or .mp3 file to create.")],
    target_seconds: Annotated[int | None, Field(
        ge=1, le=1800, description="Optional duration goal in seconds. Playback tempo can vary by up to 15%.",
    )] = None,
) -> SpeechResult:
    """Read text aloud with Indic-Speak and return a path to AI-generated WAV or MP3.

    Long text is split into short requests. The destination must be a new file
    in an existing directory. The language must match the supplied text.
    """
    destination = Path(output_path).expanduser().absolute()
    suffix = destination.suffix.lower()
    if suffix not in {".wav", ".mp3"} or not destination.parent.is_dir():
        raise ToolError("invalid_output_path: Choose a .wav or .mp3 file in an existing directory.")
    if os.path.lexists(destination):
        raise ToolError("output_exists: Choose a new audio file path.")
    if (suffix == ".mp3" or target_seconds is not None) and not shutil.which("ffmpeg"):
        raise ToolError("missing_ffmpeg: Install ffmpeg and make it available on PATH.")
    chunks = _speech_chunks(text_for_speech(text))
    key = os.getenv("BODHAN_SPEAK_KEY", "").strip()
    if not key:
        raise ToolError("missing_key: Set BODHAN_SPEAK_KEY in the recipe's .env file.")

    try:
        with TemporaryDirectory(prefix=".bodhan-speech-", dir=destination.parent) as directory:
            temporary = Path(directory) / "speech.wav"
            with httpx.Client(timeout=30.0) as client, wave.open(str(temporary), "wb") as output:
                output.setparams((1, 2, 24_000, 0, "NONE", "not compressed"))
                for index, chunk in enumerate(chunks, start=1):
                    try:
                        pcm = _speech_pcm(_request_speech(client, chunk, language, voice, key))
                    except ToolError as exc:
                        raise type(exc)(f"Speech chunk {index} of {len(chunks)}: {exc}") from None
                    output.writeframesraw(pcm)
                duration = output.getnframes() / 24_000
            tempo = min(1.15, max(0.85, duration / target_seconds)) if target_seconds else 1.0
            if suffix == ".mp3" or tempo != 1.0:
                encoded = temporary.with_name("output" + suffix)
                _encode_audio(temporary, encoded, tempo)
                temporary = encoded
                duration /= tempo
            os.link(temporary, destination)
    except FileExistsError:
        raise ToolError("output_exists: Choose a new audio file path.") from None
    except OSError:
        raise ToolError("output_error: Check permissions and free space in the output directory.") from None

    return SpeechResult(audio_path=str(destination), duration_seconds=duration, chunks=len(chunks))
