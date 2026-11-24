"""Thin client for the four Bodhan models this recipe uses.

Each Bodhan key is tied to one model, so each model reads its own env var.
`BODHAN_API_KEY` is used as a fallback for any model without its own key.
Keys are read from the environment only and never leave the server process.
"""

from __future__ import annotations

import json
import os
import time

import requests

BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")

KEY_VARS = {
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-transliterate": "BODHAN_TRANSLITERATE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
}

# indic-transcribe and indic-speak accept about 30 seconds per request.
MAX_AUDIO_SECONDS = 30


class BodhanError(RuntimeError):
    pass


def _headers(model: str) -> dict[str, str]:
    key = os.environ.get(KEY_VARS[model]) or os.environ.get("BODHAN_API_KEY")
    if not key:
        raise BodhanError(f"Missing {KEY_VARS[model]} (or BODHAN_API_KEY). See .env.example.")
    return {"Authorization": f"Bearer {key}"}


def _post(model: str, path: str, retries: int = 2, extra_headers: dict | None = None,
          **kwargs) -> requests.Response:
    """POST with a short backoff on 429 (rate limit), as CONTRIBUTING.md asks."""
    headers = {**_headers(model), **(extra_headers or {})}
    for attempt in range(retries + 1):
        resp = requests.post(f"{BASE_URL}{path}", headers=headers, timeout=120, **kwargs)
        if resp.status_code == 429 and attempt < retries:
            time.sleep(2 ** (attempt + 1))
            continue
        if resp.ok:
            return resp
        try:
            err = resp.json()["error"]
            raise BodhanError(f"{resp.status_code} {err.get('code')}: {err.get('message')}")
        except (ValueError, KeyError):
            raise BodhanError(f"{resp.status_code}: {resp.text[:200]}") from None
    raise BodhanError("Rate limited (429). Please wait a moment and try again.")


def speak(text: str, voice: str, lang: str, style: str | None = None) -> bytes:
    """Text to speech. Returns WAV bytes (PCM16, 24 kHz, mono)."""
    instructions = {"lang": lang}
    if style:
        instructions["style"] = style
    resp = _post(
        "indic-speak", "/v1/audio/speech",
        extra_headers={"Content-Type": "application/json"},
        json={"model": "indic-speak", "input": text, "voice": voice,
              "instructions": json.dumps(instructions)},
    )
    return resp.content


def transcribe(audio: bytes, language: str, filename: str = "attempt.wav") -> str:
    """Speech to text for one clip of at most ~30 seconds."""
    resp = _post(
        "indic-transcribe", "/v1/audio/transcriptions",
        files={"file": (filename, audio, "audio/wav")},
        data={"model": "indic-transcribe", "language": language},
    )
    return resp.json()["text"]


def _chat(model: str, text: str, **fields) -> str:
    """Call a text model through the OpenAI-style chat route.

    The plain /translate and /transliterate routes returned 404 on 24 Nov 2026
    (cookbook issue #34), while /v1/chat/completions works for the same models.
    """
    resp = _post(
        model, "/v1/chat/completions",
        extra_headers={"Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": text}], **fields},
    )
    return resp.json()["choices"][0]["message"]["content"].strip()


def transliterate(text: str, language: str, script: str = "roman") -> str:
    """Native script -> roman script (or back), keeping the pronunciation."""
    return _chat("indic-transliterate", text, target_language=language, script=script)


def translate(text: str, target_language: str) -> str:
    """Translate text (source language is auto-detected)."""
    return _chat("indic-translate", text, target_language=target_language)
