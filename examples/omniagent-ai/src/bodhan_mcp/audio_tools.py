
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BODHAN_BASE_URL = os.getenv(
    "BODHAN_BASE_URL",
    "https://api.bodhan.ai/v1"
).rstrip("/")

SUPPORTED_AUDIO_FORMATS = {
    ".wav", ".mp3", ".flac", ".m4a"
}


def transcribe_audio(
    audio_path: str,
    language: str = "hi"
) -> dict:
    """
    Transcribe audio using Bodhan Indic-Transcribe.

    Args:
        audio_path: Path to the audio file.
        language: Language code, e.g. hi.
    """

    if not audio_path.strip():
        return {
            "status": "error",
            "message": "Audio file path cannot be empty."
        }

    path = Path(audio_path).expanduser()

    if not path.is_file():
        return {
            "status": "error",
            "message": f"Audio file not found: {audio_path}"
        }

    if path.suffix.lower() not in SUPPORTED_AUDIO_FORMATS:
        return {
            "status": "error",
            "message": "Unsupported audio format."
        }

    if path.stat().st_size == 0:
        return {
            "status": "error",
            "message": "Audio file is empty."
        }

    api_key = os.getenv("BODHAN_TRANSCRIBE_KEY")

    if not api_key:
        return {
            "status": "error",
            "message": "BODHAN_TRANSCRIBE_KEY is missing from .env"
        }

    try:
        with path.open("rb") as audio_file:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{BODHAN_BASE_URL}/audio/transcriptions",
                    headers={
                        "Authorization": f"Bearer {api_key}"
                    },
                    files={
                        "file": (
                            path.name,
                            audio_file
                        )
                    },
                    data={
                        "model": "indic-transcribe",
                        "language": language,
                        "vad": "true"
                    }
                )

                response.raise_for_status()
                data = response.json()

        transcription = data["text"]

        return {
            "status": "success",
            "audio_path": str(path.resolve()),
            "language": language,
            "transcription": transcription,
            "model": "indic-transcribe"
        }

    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "message": (
                f"Bodhan API returned HTTP "
                f"{exc.response.status_code}"
            )
        }

    except httpx.RequestError as exc:
        return {
            "status": "error",
            "message": (
                f"Network request failed: "
                f"{type(exc).__name__}"
            )
        }

    except (KeyError, TypeError, ValueError):
        return {
            "status": "error",
            "message": "Unexpected Bodhan API response format."
        }


