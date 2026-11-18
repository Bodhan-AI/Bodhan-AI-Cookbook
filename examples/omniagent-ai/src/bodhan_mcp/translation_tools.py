
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BODHAN_BASE_URL = os.getenv(
    "BODHAN_BASE_URL",
    "https://api.bodhan.ai/v1"
).rstrip("/")


SUPPORTED_LANGUAGES = {
    "en", "hi", "ta", "bn", "mr", "te",
    "kn", "ml", "gu", "pa", "or"
}


def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
) -> dict:
    """Translate text using Bodhan Indic-Translate."""

    if not text.strip():
        return {
            "status": "error",
            "message": "Translation text cannot be empty."
        }

    if source_lang not in SUPPORTED_LANGUAGES:
        return {
            "status": "error",
            "message": f"Unsupported source language: {source_lang}"
        }

    if target_lang not in SUPPORTED_LANGUAGES:
        return {
            "status": "error",
            "message": f"Unsupported target language: {target_lang}"
        }

    if source_lang == target_lang:
        return {
            "status": "error",
            "message": "Source and target languages must differ."
        }

    api_key = os.getenv("BODHAN_TRANSLATE_KEY")

    if not api_key:
        return {
            "status": "error",
            "message": "BODHAN_TRANSLATE_KEY is missing from .env"
        }

    payload = {
        "model": "indic-translate",
        "messages": [
            {
                "role": "user",
                "content": text
            }
        ],
        "target_language_code": target_lang,
        "target_script": "native"
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{BODHAN_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=payload
            )

            response.raise_for_status()
            data = response.json()

        translated_text = data["choices"][0]["message"]["content"]

        return {
            "status": "success",
            "source_lang": source_lang,
            "target_lang": target_lang,
            "translated_text": translated_text,
            "model": "indic-translate"
        }

    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "message": f"Bodhan API returned HTTP {exc.response.status_code}"
        }

    except httpx.RequestError as exc:
        return {
            "status": "error",
            "message": f"Network request failed: {type(exc).__name__}"
        }

    except (KeyError, IndexError, TypeError, ValueError):
        return {
            "status": "error",
            "message": "Unexpected Bodhan API response format."
        }
