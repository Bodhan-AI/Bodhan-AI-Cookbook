"""The four Bodhan API tools. File contents stay local; traces store references only."""

from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any

import requests

from .config import Settings


class BodhanError(RuntimeError):
    pass


def _request(
    settings: Settings, method: str, path: str, *, json_body: dict | None = None, files: dict | None = None, data: dict | None = None
) -> requests.Response:
    url = settings.bodhan_base_url + path
    for attempt in range(settings.max_retries + 1):
        try:
            response = requests.request(
                method,
                url,
                headers={"Authorization": f"Bearer {settings.bodhan_api_key}"},
                json=json_body,
                files=files,
                data=data,
                timeout=settings.timeout_seconds,
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < settings.max_retries:
                    time.sleep(min(2**attempt, 8))
                    continue
                raise BodhanError(f"Bodhan returned HTTP {response.status_code} after bounded retries.")
            if response.status_code >= 400:
                raise BodhanError(f"Bodhan rejected the request with HTTP {response.status_code}.")
            return response
        except requests.RequestException as exc:
            if attempt >= settings.max_retries:
                raise BodhanError(f"Bodhan request failed ({type(exc).__name__}); check connectivity, credentials, and API status.") from exc
            time.sleep(min(2**attempt, 8))
    raise BodhanError("Bodhan request failed after retries.")


def indic_asr(settings: Settings, audio_path: str, language: str | None = None) -> dict[str, Any]:
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise BodhanError(f"Audio file not found: {path}")
    with path.open("rb") as audio:
        response = _request(
            settings,
            "POST",
            "/v1/audio/transcriptions",
            files={"file": (path.name, audio, mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
            data={"model": "indic-transcribe", **({"language": language} if language else {})},
        )
    payload = response.json()
    return {"text": payload.get("text", ""), "usage": payload.get("usage"), "cost_inr": payload.get("cost_inr")}


def indic_translate(settings: Settings, text: str, target_language: str, script: str = "native") -> dict[str, Any]:
    # Cookbook-supported targets are explicitly constrained to Indic/English codes.
    supported = {
        "as",
        "bn",
        "brx",
        "doi",
        "en",
        "gu",
        "hi",
        "kn",
        "kok",
        "ks",
        "ks-Deva",
        "mai",
        "ml",
        "mni",
        "mni-Beng",
        "mr",
        "ne",
        "or",
        "pa",
        "sa",
        "sat",
        "sd",
        "sd-Arab",
        "ta",
        "te",
        "ur",
    }
    if target_language not in supported:
        raise BodhanError(
            f"Unsupported target language '{target_language}'. indic-translate supports listed Indic languages and English; French is not supported."
        )
    if script not in {"native", "roman", "codemix"}:
        raise BodhanError("script must be native, roman, or codemix")
    payload = _request(settings, "POST", "/translate", json_body={"text": text, "target_language": target_language, "script": script}).json()
    return {"text": payload.get("translation", ""), "usage": payload.get("usage"), "cost_inr": payload.get("cost_inr")}


def indic_tts(settings: Settings, text: str, language: str, output_path: str, voice: str, style: str | None = None) -> dict[str, Any]:
    if not voice.strip():
        raise BodhanError("Choose a Bodhan voice for the requested language; see scripts/bodhan_api_rules.json.")
    body: dict[str, Any] = {
        "model": "indic-speak",
        "input": text,
        "voice": voice,
        "instructions": __import__("json").dumps({"lang": language, **({"style": style} if style else {})}),
    }
    response = _request(settings, "POST", "/v1/audio/speech", json_body=body)
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)
    return {"media_ref": str(output), "content_type": response.headers.get("Content-Type", "audio/wav"), "cost_inr": response.headers.get("X-Cost-INR")}


def indic_ocr(settings: Settings, image_path: str, prompt: str = "Extract all visible text, preserving layout.") -> dict[str, Any]:
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise BodhanError(f"Image file not found: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = _request(
        settings,
        "POST",
        "/v1/chat/completions",
        json_body={
            "model": "indic-ocr",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}]}
            ],
        },
    ).json()
    text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {"text": text, "usage": payload.get("usage"), "cost_inr": payload.get("cost_inr")}
