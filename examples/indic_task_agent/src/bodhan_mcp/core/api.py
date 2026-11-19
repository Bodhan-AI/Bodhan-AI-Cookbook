"""Request/response shapes for the four model routes, in one place.

The translate route is the one published docs and live reports disagree on:
docs say `POST /translate`, four independent live runs in competing submissions
say that route 404s and `/v1/chat/completions` with `model: indic-translate`
works. Both shapes are supported here; the chat route is tried first and a 404
falls through. One smoke call settles which is current — see README.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from . import client, config
from .errors import BodhanError

TRANSLATE_CHAT = "/v1/chat/completions"
TRANSLATE_DIRECT = "/translate"


def _field(body: dict[str, Any], *names: str) -> Any:
    for n in names:
        if n in body:
            return body[n]
    return None


def _usage_tokens(body: dict[str, Any]) -> int:
    usage = body.get("usage") or {}
    return int(usage.get("completion_tokens") or usage.get("output_tokens") or body.get("output_tokens") or 0)


def translate(text: str, target: str, script: str = "native", max_output_tokens: int | None = None) -> dict[str, Any]:
    """Call Bodhan's translator once. Returns {text, tokens, truncated}."""
    body = {
        "model": config.MODELS["mt"],
        "messages": [{"role": "user", "content": text}],
        "target_language_code": target,
        "target_script": script,
    }
    if max_output_tokens:
        body["max_output_tokens"] = max_output_tokens
    resp = client.post("mt", TRANSLATE_CHAT, json=body)
    data = resp.json()
    translation = _field(data, "translation", "translated_text")
    if translation is None:
        try:
            translation = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise BodhanError("UPSTREAM_ERROR", "indic-translate returned no translation field.") from None
    finish = None
    try:
        finish = data["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return {
        "text": translation,
        "tokens": _usage_tokens(data),
        "truncated": bool(_field(data, "truncated") or finish == "length"),
    }


def translate_direct(text: str, target: str, script: str = "native", max_output_tokens: int | None = None) -> dict[str, Any]:
    """Fallback for the documented plain-JSON route, kept for the day the chat route changes."""
    body: dict[str, Any] = {"text": text, "target_language": target}
    if script != "native":
        body["script"] = script
    if max_output_tokens:
        body["max_output_tokens"] = max_output_tokens
    data = client.post("mt", TRANSLATE_DIRECT, json=body).json()
    return {
        "text": _field(data, "translation", "translated_text") or "",
        "tokens": _usage_tokens(data),
        "truncated": bool(_field(data, "truncated")),
    }


def transcribe(audio_wav: bytes, language: str | None, filename: str = "audio.wav") -> str:
    """One ASR request for <=30 s of WAV. Returns transcript text, unwrapping a nested JSON text field."""
    data = {"model": config.MODELS["asr"]}
    if language:
        data["language"] = language
    resp = client.post("asr", "/v1/audio/transcriptions", data=data, files={"file": (filename, audio_wav, "audio/wav")})
    body = resp.json()
    text = _field(body, "text")
    if isinstance(text, str) and text.strip().startswith("{"):
        try:
            inner = json.loads(text)
            if isinstance(inner, dict) and isinstance(inner.get("text"), str):
                text = inner["text"]
        except ValueError:
            pass
    return (text or "").strip()


def speak(text: str, language: str, voice: str, style: str | None) -> bytes:
    """One TTS request. Returns the audio bytes as sent (WAV or MP3 — detected by the caller)."""
    instructions: dict[str, str] = {"lang": language}
    if style:
        instructions["style"] = style
    body = {"model": config.MODELS["tts"], "input": text, "voice": voice, "instructions": json.dumps(instructions)}
    return client.post("tts", "/v1/audio/speech", json=body).content


def ocr_page(jpeg: bytes, table_format: str, max_tokens: int) -> dict[str, Any]:
    """One OCR request for one JPEG page. Returns {content, blocks, detail}."""
    body = {
        "model": config.MODELS["ocr"],
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]}
        ],
        "table_format": table_format,
        "max_tokens": max_tokens,
    }
    resp = client.post("ocr", "/v1/chat/completions", json=body, retry_5xx=False)
    data = resp.json()
    content, finish = "", None
    try:
        content = data["choices"][0]["message"]["content"] or ""
        finish = data["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError):
        pass
    return {"content": content, "blocks": data.get("blocks") or [], "detail": data.get("detail"), "finish_reason": finish}
