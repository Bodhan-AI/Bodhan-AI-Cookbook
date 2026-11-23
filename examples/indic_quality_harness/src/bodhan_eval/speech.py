import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

RECIPE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(RECIPE_DIR / ".env")


class BodhanSpeechError(Exception):
    """Raised when a Bodhan speech API request fails."""


class BodhanSpeechClient:
    def __init__(self):
        self.base_url = os.getenv(
            "BODHAN_BASE_URL",
            "https://api.bodhan.ai",
        ).rstrip("/")

        # Service-specific keys win; BODHAN_API_KEY is the shared fallback.
        shared_key = os.getenv("BODHAN_API_KEY")
        self.speak_key = os.getenv("BODHAN_SPEAK_API_KEY") or shared_key
        self.transcribe_key = os.getenv("BODHAN_TRANSCRIBE_API_KEY") or shared_key

        if not self.speak_key:
            raise ValueError("BODHAN_SPEAK_API_KEY (or BODHAN_API_KEY) is missing.")

        if not self.transcribe_key:
            raise ValueError("BODHAN_TRANSCRIBE_API_KEY (or BODHAN_API_KEY) is missing.")

        self.session = requests.Session()

    def speak(
        self,
        text: str,
        language: str,
        output_path: str,
        voice: str = "Amit",
    ) -> Path:
        """Convert Indic text into speech and save the returned audio."""

        if not text.strip():
            raise ValueError("Speech input cannot be empty.")

        payload = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": json.dumps({"lang": language}),
        }

        try:
            response = self.session.post(
                f"{self.base_url}/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {self.speak_key}",
                },
                json=payload,
                timeout=120,
            )
            response.raise_for_status()

            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(response.content)

            if not response.content:
                raise BodhanSpeechError("Speech API returned an empty audio file.")

            return output

        except requests.RequestException as exc:
            raise BodhanSpeechError(
                f"Speech generation failed: {exc}"
            ) from exc

    def transcribe(
        self,
        audio_path: str,
        language: str,
    ) -> str:
        """Transcribe a WAV file into text using Indic-Transcribe."""

        audio_file = Path(audio_path)

        if not audio_file.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        try:
            with audio_file.open("rb") as audio:
                response = self.session.post(
                    f"{self.base_url}/v1/audio/transcriptions",
                    headers={
                        "Authorization": f"Bearer {self.transcribe_key}",
                    },
                    files={
                        "file": (audio_file.name, audio, "audio/wav"),
                    },
                    data={
                        "model": "indic-transcribe",
                        "language": language,
                        "vad": "true",
                    },
                    timeout=120,
                )

            response.raise_for_status()
            result = response.json()

            if "text" not in result:
                raise BodhanSpeechError(
                    "Transcription response did not contain text."
                )

            return result["text"]

        except requests.RequestException as exc:
            raise BodhanSpeechError(
                f"Transcription failed: {exc}"
            ) from exc
