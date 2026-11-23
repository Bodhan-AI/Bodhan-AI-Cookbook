import os
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.bodhan.ai"

# Load the recipe's .env file, not the repository root's .env.
RECIPE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(RECIPE_DIR / ".env")


class BodhanAPIError(Exception):
    """Raised when a Bodhan API request fails."""


class BodhanClient:
    def __init__(self, api_key: str | None = None):
        # A service-specific key wins; BODHAN_API_KEY (the Cookbook-wide
        # convention) is the fallback.
        self.api_key = (
            api_key
            or os.getenv("BODHAN_TRANSLATE_API_KEY")
            or os.getenv("BODHAN_API_KEY")
        )
        self.base_url = os.getenv(
            "BODHAN_BASE_URL",
            "https://api.bodhan.ai",
        ).rstrip("/")

        if not self.api_key:
            raise ValueError(
                "BODHAN_TRANSLATE_API_KEY (or BODHAN_API_KEY) is missing."
            )

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
        })

    def translate(
        self,
        text: str,
        target_language: str,
        script: str = "native",
    ) -> dict:
        """Translate using Bodhan's OpenAI-compatible endpoint."""

        if not text.strip():
            raise ValueError("Text cannot be empty.")

        payload = {
            "model": "indic-translate",
            "messages": [
                {"role": "user", "content": text}
            ],
            "target_language_code": target_language,
            "target_script": script,
        }

        try:
            response = self.session.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            body = response.json()

            if "error" in body:
                raise BodhanAPIError(body["error"].get("code", "unknown_error"))

            translation = body["choices"][0]["message"]["content"]

            return {
                "translation": translation,
                "truncated": body["choices"][0].get("finish_reason") == "length",
                "usage": body.get("usage", {}),
            }

        except requests.RequestException as exc:
            raise BodhanAPIError(
                f"Translation request failed: {exc}"
            ) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise BodhanAPIError(
                f"Unexpected translation response: {exc}"
            ) from exc