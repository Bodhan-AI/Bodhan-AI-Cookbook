"""Synthesize short passages on Bodhan and assemble a local WAV artifact."""

import io
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import textwrap
from typing import Annotated, Literal
import wave

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

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
        response = client.post(
            SPEECH_URL,
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
            429: "rate_limited: Wait before retrying.",
        }
        raise ToolError(errors.get(response.status_code, f"upstream_error: Bodhan returned HTTP {response.status_code}."))
    return response.content


def _speech_pcm(wav_bytes: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 24_000):
                raise ToolError("invalid_audio: Expected 24 kHz mono PCM16 from Bodhan.")
            frames = source.readframes(source.getnframes())
    except (EOFError, wave.Error):
        raise ToolError("invalid_audio: Bodhan returned unreadable WAV audio.") from None
    if not frames or len(frames) % 2:
        raise ToolError("invalid_audio: Bodhan returned empty or malformed audio.")
    return frames


def synthesize_speech(
    text: Annotated[str, Field(min_length=1, description="Text to read aloud in the requested language.")],
    language: Annotated[SpeechLanguage, Field(description="Language of the text, such as kn for Kannada.")],
    voice: Annotated[Voice, Field(description="A voice from Bodhan's voice library.")],
    output_path: Annotated[str, Field(min_length=1, description="New local .wav file to create.")],
) -> SpeechResult:
    """Read text aloud with Indic-Speak and return a path to the AI-generated WAV.

    Long text is split into short requests. The destination must be a new file
    in an existing directory. The language must match the supplied text.
    """
    destination = Path(output_path).expanduser().absolute()
    if destination.suffix.lower() != ".wav" or not destination.parent.is_dir():
        raise ToolError("invalid_output_path: Choose a .wav file in an existing directory.")
    if os.path.lexists(destination):
        raise ToolError("output_exists: Choose a new WAV file path.")
    chunks = _speech_chunks(text)
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
                        raise ToolError(f"Speech chunk {index} of {len(chunks)}: {exc}") from None
                    output.writeframesraw(pcm)
                duration = output.getnframes() / 24_000
            os.link(temporary, destination)
    except FileExistsError:
        raise ToolError("output_exists: Choose a new WAV file path.") from None
    except OSError:
        raise ToolError("output_error: Check permissions and free space in the output directory.") from None

    return SpeechResult(audio_path=str(destination), duration_seconds=duration, chunks=len(chunks))
