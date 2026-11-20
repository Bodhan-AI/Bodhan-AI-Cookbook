
import logging
import os
import tempfile
from pathlib import Path

import requests
import soundfile as sf
from dotenv import load_dotenv
logger = logging.getLogger(__name__)

load_dotenv()

BODHAN_BASE_URL = os.getenv(
    "BODHAN_BASE_URL",
    "https://api.bodhan.ai"
)

TRANSCRIBE_URL = (
    f"{BODHAN_BASE_URL}/v1/audio/transcriptions"
)

# Stay below Bodhan's 30-second request limit.
CHUNK_DURATION_SECONDS = 29.0


def _transcribe_chunk(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
) -> str:
    """Send one audio chunk to Bodhan Indic-Transcribe."""

    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": "indic-transcribe"
    }

    if language:
        data["language"] = language

    with audio_path.open("rb") as audio_file:
        files = {
            "file": (
                audio_path.name,
                audio_file
            )
        }

        response = requests.post(
            TRANSCRIBE_URL,
            headers=headers,
            data=data,
            files=files,
            timeout=60,
        )

    if response.status_code == 401:
        raise RuntimeError(
            "Bodhan authentication failed. "
            "Check your transcription API key."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Bodhan rate limit or credit limit reached."
        )

    if response.status_code == 415:
        raise RuntimeError(
            "Bodhan rejected an audio chunk. "
            "Check the supported audio format."
        )

    response.raise_for_status()

    result = response.json()

    if not isinstance(result.get("text"), str):
        raise RuntimeError(
            "Unexpected Bodhan response: "
            "transcription text is missing."
        )

    return result["text"]


def transcribe_audio(
    file_path: str,
    language: str | None = None,
) -> dict:
    """
    Transcribe audio using Bodhan Indic-Transcribe.

    Automatically splits long audio into chunks of
    at most 29 seconds and combines their transcripts.
    """

    api_key = os.getenv("BODHAN_TRANSCRIBE_KEY")

    if not api_key:
        raise ValueError(
            "BODHAN_TRANSCRIBE_KEY is not configured."
        )

    audio_path = Path(file_path)

    if not audio_path.exists():
        raise FileNotFoundError(
            f"Audio file not found: {file_path}"
        )

    if not audio_path.is_file():
        raise ValueError(
            f"Expected an audio file: {file_path}"
        )

    try:
        audio, sample_rate = sf.read(
            str(audio_path),
            dtype="float32",
            always_2d=True,
        )
    except (OSError, RuntimeError) as exc:
        raise ValueError(
            "Unable to read this audio file. "
            "Use a format supported by SoundFile."
        ) from exc

    if audio.size == 0:
        raise ValueError("Audio file is empty.")

    duration = len(audio) / sample_rate

    if duration <= 0:
        raise ValueError("Audio duration must be positive.")

    chunk_samples = int(
        CHUNK_DURATION_SECONDS * sample_rate
    )

    transcripts = []

    # Short audio: send the original file directly.
    if duration <= 30:
        text = _transcribe_chunk(
            audio_path,
            api_key,
            language,
        )

        transcripts.append(text)

    else:
        # Long audio: create temporary WAV chunks.
        with tempfile.TemporaryDirectory() as temp_dir:

            temp_path = Path(temp_dir)

            total_samples = len(audio)

            chunk_number = 0

            for start in range(
                0,
                total_samples,
                chunk_samples,
            ):

                end = min(
                    start + chunk_samples,
                    total_samples,
                )

                chunk_audio = audio[start:end]

                chunk_number += 1

                chunk_path = (
                    temp_path
                    / f"chunk_{chunk_number:03d}.wav"
                )

                sf.write(
                    str(chunk_path),
                    chunk_audio,
                    sample_rate,
                    subtype="PCM_16",
                )

                logger.info(
                "Transcribing audio chunk %s...",
                chunk_number
            )

                chunk_text = _transcribe_chunk(
                    chunk_path,
                    api_key,
                    language,
                )

                transcripts.append(chunk_text)

    combined_text = "\n".join(
        text.strip()
        for text in transcripts
        if text.strip()
    )

    return {
        "text": combined_text,
        "model": "indic-transcribe",
        "file": audio_path.name,
        "duration_seconds": round(duration, 2),
        "chunks_processed": len(transcripts),
    }
