
import io
import json
import logging
import os
import re
from pathlib import Path

import numpy as np
import requests
import soundfile as sf
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BODHAN_BASE_URL = os.getenv(
    "BODHAN_BASE_URL",
    "https://api.bodhan.ai"
)

SPEAK_URL = f"{BODHAN_BASE_URL}/v1/audio/speech"

# Conservative text size per request.
# This is our internal chunking setting, not a claimed
# official Bodhan character limit.
MAX_CHUNK_CHARACTERS = 250


def _split_text(text: str) -> list[str]:
    """Split long text into manageable sentence-based chunks."""

    text = text.strip()

    if not text:
        raise ValueError("Input text cannot be empty.")

    sentences = re.split(
        r'(?<=[.!?।])\s+',
        text
    )

    chunks = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        # Handle a sentence longer than our chunk size.
        if len(sentence) > MAX_CHUNK_CHARACTERS:

            if current:
                chunks.append(current)
                current = ""

            words = sentence.split()
            part = ""

            for word in words:
                candidate = (
                    f"{part} {word}".strip()
                )

                if len(candidate) > MAX_CHUNK_CHARACTERS:
                    if part:
                        chunks.append(part)

                    part = word
                else:
                    part = candidate

            if part:
                current = part

            continue

        candidate = (
            f"{current} {sentence}".strip()
        )

        if len(candidate) > MAX_CHUNK_CHARACTERS:
            if current:
                chunks.append(current)

            current = sentence
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def _request_speech(
    text: str,
    api_key: str,
    language: str,
    voice: str,
    style: str = "",
) -> tuple[np.ndarray, int]:
    """Generate one WAV audio segment using Bodhan."""

    instructions = {
        "lang": language
    }

    if style:
        instructions["style"] = style

    payload = {
        "model": "indic-speak",
        "input": text,
        "voice": voice,
        "instructions": json.dumps(instructions),
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        SPEAK_URL,
        headers=headers,
        json=payload,
        timeout=90,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Bodhan authentication failed. "
            "Check your speech API key."
        )

    if response.status_code == 422:
        raise RuntimeError(
            f"Bodhan rejected the speech request: "
            f"{response.text[:500]}"
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Bodhan rate limit or credit limit reached."
        )

    response.raise_for_status()

    if not response.content.startswith(b"RIFF"):
        raise RuntimeError(
            "Bodhan did not return a valid WAV audio file."
        )

    try:
        audio, sample_rate = sf.read(
            io.BytesIO(response.content),
            dtype="float32",
            always_2d=True,
        )
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(
            "Bodhan returned an unreadable WAV file."
        ) from exc

    if audio.size == 0:
        raise RuntimeError(
            "Bodhan returned an empty audio segment."
        )

    return audio, sample_rate


def speak_text(
    text: str,
    language: str,
    voice: str,
    output_path: str,
    style: str = "",
) -> dict:
    """
    Convert text into speech using Bodhan Indic-Speak.

    Long text is split into smaller chunks.
    The generated WAV segments are combined into
    one final WAV file.
    """

    api_key = os.getenv("BODHAN_SPEAK_KEY")

    if not api_key:
        raise ValueError(
            "BODHAN_SPEAK_KEY is not configured."
        )

    if not language or not language.strip():
        raise ValueError("Language is required.")

    if not voice or not voice.strip():
        raise ValueError("Voice is required.")

    audio_path = Path(output_path)

    if audio_path.suffix.lower() != ".wav":
        raise ValueError(
            "Output file must have a .wav extension."
        )

    chunks = _split_text(text)

    audio_segments = []
    expected_sample_rate = None
    expected_channels = None

    for index, chunk in enumerate(chunks, start=1):

        logger.info(
            "Generating speech chunk %s of %s",
            index,
            len(chunks),
        )

        audio, sample_rate = _request_speech(
            text=chunk,
            api_key=api_key,
            language=language,
            voice=voice,
            style=style,
        )

        if expected_sample_rate is None:
            expected_sample_rate = sample_rate
            expected_channels = audio.shape[1]

        elif (
            sample_rate != expected_sample_rate
            or audio.shape[1] != expected_channels
        ):
            raise RuntimeError(
                "Bodhan returned audio segments with "
                "incompatible sample rates or channels."
            )

        audio_segments.append(audio)

    combined_audio = np.concatenate(
        audio_segments,
        axis=0,
    )

    audio_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    sf.write(
        str(audio_path),
        combined_audio,
        expected_sample_rate,
        subtype="PCM_16",
    )

    return {
        "model": "indic-speak",
        "file": str(audio_path),
        "language": language,
        "voice": voice,
        "chunks_processed": len(chunks),
        "duration_seconds": round(
            len(combined_audio) / expected_sample_rate,
            2,
        ),
        "size_bytes": audio_path.stat().st_size,
    }
