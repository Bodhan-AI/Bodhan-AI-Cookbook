"""Svara: Core AAC speech engine powered by Bodhan indic-speak.

Provides low-latency speech synthesis, code-mixed bilingual support,
voice personalization, and robust audio caching for non-verbal individuals.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

DEFAULT_BASE_URL = "https://api.bodhan.ai"

# Reference voice mapping from scripts/bodhan_api_rules.json
INDIC_VOICES: dict[str, dict[str, list[str]]] = {
    "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]},
    "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]},
}

SUPPORTED_STYLES = [
    "neutral",
    "happy",
    "sad",
    "anger",
    "fear",
    "surprise",
    "Customer Care",
    "news",
]

DEFAULT_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "emergency",
        "name": "Emergency & Medical",
        "phrases": [
            {"label": "Need Help", "text": "Please help me, emergency hai.", "style": "fear"},
            {"label": "Call Doctor", "text": "Doctor ko call karo please.", "style": "anger"},
            {"label": "Medicine", "text": "Mujhe medicine chahiye.", "style": "neutral"},
            {"label": "In Pain", "text": "Mujhe bahut pain ho raha hai.", "style": "sad"},
        ],
    },
    {
        "id": "daily_needs",
        "name": "Daily Needs",
        "phrases": [
            {"label": "Water", "text": "Mujhe thoda water chahiye, please.", "style": "neutral"},
            {"label": "Food", "text": "Mujhe khana khana hai, bhookh lagi hai.", "style": "neutral"},
            {"label": "Rest", "text": "Main thak gaya hoon, mujhe rest karna hai.", "style": "neutral"},
            {"label": "Washroom", "text": "Mujhe washroom jaana hai.", "style": "neutral"},
        ],
    },
    {
        "id": "social",
        "name": "Social & Responses",
        "phrases": [
            {"label": "Namaste", "text": "Namaste, aap kaise hain?", "style": "happy"},
            {"label": "Thank You", "text": "Aapka bahut bahut thank you.", "style": "happy"},
            {"label": "Yes", "text": "Haan, bilkul.", "style": "neutral"},
            {"label": "No", "text": "Nahi, abhi nahi.", "style": "neutral"},
        ],
    },
    {
        "id": "feelings",
        "name": "Feelings",
        "phrases": [
            {"label": "Happy", "text": "Main aaj bahut happy hoon.", "style": "happy"},
            {"label": "Tired", "text": "Main thoda tired feel kar raha hoon.", "style": "sad"},
            {"label": "Okay", "text": "Sab theek hai, don't worry.", "style": "neutral"},
            {"label": "Listen", "text": "Meri baat suniye please.", "style": "neutral"},
        ],
    },
]


class SvaraClient:
    """Client for Bodhan Indic-Speak TTS with caching and resilient backoff."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_dir: str | Path = "audio_cache",
    ) -> None:
        self.api_key = api_key or os.environ.get("BODHAN_API_KEY") or os.environ.get("BODHAN_SPEAK_KEY")
        if not self.api_key:
            raise ValueError(
                "Bodhan API key not found. Set BODHAN_API_KEY in your environment or pass api_key."
            )

        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get_cache_path(self, text: str, voice: str, lang: str, style: str | None) -> Path:
        """Derive a deterministic file path from synthesis parameters."""
        raw_key = f"{lang}:{voice}:{style or 'neutral'}:{text.strip()}"
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{lang}_{voice}_{key_hash}.wav"

    def synthesize(
        self,
        text: str,
        voice: str = "Kavya",
        lang: str = "hi",
        style: str | None = None,
        use_cache: bool = True,
        max_retries: int = 3,
    ) -> tuple[bytes, bool, float]:
        """Synthesize speech using Bodhan indic-speak.

        Returns:
            (audio_bytes, is_cached, latency_seconds)
        """
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Input text cannot be empty.")

        cache_path = self._get_cache_path(clean_text, voice, lang, style)

        if use_cache and cache_path.exists():
            return cache_path.read_bytes(), True, 0.0

        instructions: dict[str, str] = {"lang": lang}
        if style and style != "neutral":
            instructions["style"] = style

        payload = {
            "model": "indic-speak",
            "input": clean_text,
            "voice": voice,
            "instructions": json.dumps(instructions),
        }

        last_error = None
        for attempt in range(max_retries):
            t0 = time.perf_counter()
            try:
                resp = requests.post(
                    f"{self.base_url}/v1/audio/speech",
                    headers=self.headers,
                    json=payload,
                    timeout=120,
                )
                dt = time.perf_counter() - t0

                if resp.status_code == 429:
                    backoff = 2 * (attempt + 1)
                    time.sleep(backoff)
                    continue

                if not resp.ok:
                    try:
                        err = resp.json().get("error", {})
                        msg = err.get("message", resp.text)
                        code = err.get("code", resp.status_code)
                        raise RuntimeError(f"Bodhan API error ({code}): {msg}")
                    except (ValueError, KeyError, AttributeError):
                        resp.raise_for_status()

                audio_data = resp.content
                if use_cache:
                    cache_path.write_bytes(audio_data)

                return audio_data, False, dt

            except (requests.RequestException, RuntimeError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                else:
                    raise last_error from None

        raise RuntimeError(f"Speech synthesis failed after {max_retries} retries: {last_error}")

    def prewarm_cache(
        self,
        phrases: list[dict[str, str] | str],
        voice: str = "Kavya",
        lang: str = "hi",
        on_progress: Callable[[int, int, str, bool], None] | None = None,
    ) -> dict[str, Any]:
        """Pre-cache a collection of phrases to ensure zero-latency offline playback."""
        total = len(phrases)
        cached_count = 0
        new_count = 0

        for i, item in enumerate(phrases, 1):
            if isinstance(item, dict):
                text = item["text"]
                style = item.get("style")
            else:
                text = item
                style = None

            _, is_cached, _ = self.synthesize(
                text=text,
                voice=voice,
                lang=lang,
                style=style,
                use_cache=True,
            )

            if is_cached:
                cached_count += 1
            else:
                new_count += 1
                # Small delay to respect API gateway burst limits
                time.sleep(0.5)

            if on_progress:
                on_progress(i, total, text, is_cached)

        return {"total": total, "cached": cached_count, "generated": new_count}

    @staticmethod
    def get_voices(lang: str = "hi") -> list[str]:
        """Return available voices for the specified language."""
        lang_data = INDIC_VOICES.get(lang, INDIC_VOICES["hi"])
        return lang_data.get("female", []) + lang_data.get("male", [])

    @staticmethod
    def get_styles() -> list[str]:
        """Return supported speaking style presets."""
        return list(SUPPORTED_STYLES)

    @staticmethod
    def load_default_phrases(path: str | Path | None = None) -> list[dict[str, Any]]:
        """Load default AAC phrase categories."""
        if path is not None:
            p = Path(path)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                return data.get("categories", [])
        return [dict(cat) for cat in DEFAULT_CATEGORIES]
