
import json
import os
import wave
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BODHAN_BASE_URL = os.getenv(
    "BODHAN_BASE_URL",
    "https://api.bodhan.ai/v1"
).rstrip("/")

SUPPORTED_LANGUAGES = {
    "en", "hi", "ta", "bn", "mr",
    "te", "kn", "ml", "gu", "pa", "or"
}


def generate_speech(
    text: str,
    language: str,
    output_path: str = "out/artifacts/speech.wav"
) -> dict:
    """Generate speech using Bodhan Indic-Speak."""

    if not text.strip():
        return {
            "status": "error",
            "message": "Speech text cannot be empty."
        }

    if language not in SUPPORTED_LANGUAGES:
        return {
            "status": "error",
            "message": f"Unsupported language: {language}"
        }

    if not output_path.strip():
        return {
            "status": "error",
            "message": "Output path cannot be empty."
        }

    path = Path(output_path).expanduser()

    if path.suffix.lower() != ".wav":
        return {
            "status": "error",
            "message": "Output file must use .wav extension."
        }

    api_key = os.getenv("BODHAN_SPEAK_KEY")

    if not api_key:
        return {
            "status": "error",
            "message": "BODHAN_SPEAK_KEY is missing from .env"
        }

    payload = {
        "model": "indic-speak",
        "input": text,
        "voice": "Amit",
        "instructions": json.dumps({
            "lang": language
        })
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{BODHAN_BASE_URL}/audio/speech",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=payload
            )

            response.raise_for_status()

            audio_data = response.content

        if not audio_data:
            return {
                "status": "error",
                "message": "Bodhan returned empty audio data."
            }

        # Confirm that the response is a valid WAV file.
        import io

        try:
            with wave.open(io.BytesIO(audio_data), "rb") as wav:
                duration_seconds = (
                    wav.getnframes() / wav.getframerate()
                )
        except (wave.Error, EOFError, ZeroDivisionError):
            return {
                "status": "error",
                "message": "Bodhan did not return valid WAV audio."
            }

        path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        path.write_bytes(audio_data)

        return {
            "status": "success",
            "language": language,
            "output_path": str(path.resolve()),
            "duration_seconds": round(duration_seconds, 2),
            "model": "indic-speak"
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

    except OSError as exc:
        return {
            "status": "error",
            "message": f"Could not save audio: {type(exc).__name__}"
        }
