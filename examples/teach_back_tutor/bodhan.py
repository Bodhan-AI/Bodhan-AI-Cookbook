"""Thin client for the five Bodhan APIs used by the teach-back tutor."""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import wave
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")


_KEY_VARS = {
    "ocr": "BODHAN_OCR_KEY",
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "transliterate": "BODHAN_TRANSLITERATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
}


def _headers(api: str) -> dict:
    """A per-API key wins; otherwise fall back to the single BODHAN_API_KEY."""
    key = os.environ.get(_KEY_VARS[api]) or os.environ.get("BODHAN_API_KEY")
    if not key:
        raise BodhanError(f"set {_KEY_VARS[api]} or BODHAN_API_KEY in .env")
    return {"Authorization": f"Bearer {key}"}


class BodhanError(RuntimeError):
    pass


def _check(resp: requests.Response) -> requests.Response:
    if resp.ok:
        return resp
    try:
        err = resp.json()["error"]
        msg = f"{resp.status_code} {err.get('code')}: {err.get('message')} (request_id={err.get('request_id')})"
    except (ValueError, KeyError, TypeError):
        msg = f"{resp.status_code}: {resp.text[:300]}"
    raise BodhanError(msg)


def ocr_page(image_bytes: bytes, filename: str = "page.png") -> str:
    """Return the page as Markdown."""
    mime = mimetypes.guess_type(filename)[0] or "image/png"
    b64 = base64.b64encode(image_bytes).decode()
    body = {
        "model": "indic-ocr",
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
        "table_format": "markdown",
    }
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", headers=_headers("ocr"), json=body, timeout=180)
    return _check(resp).json()["choices"][0]["message"]["content"]


def transcribe(audio_bytes: bytes, language: str | None = None, filename: str = "audio.wav") -> str:
    data = {"model": "indic-transcribe"}
    if language:
        data["language"] = language
    resp = requests.post(
        f"{BASE_URL}/v1/audio/transcriptions",
        headers=_headers("transcribe"),
        files={"file": (filename, audio_bytes)},
        data=data,
        timeout=60,
    )
    return _check(resp).json()["text"]


# The documented /translate and /transliterate routes return 404 (cookbook issue #34);
# both models answer on the OpenAI-style chat route with their options as top-level fields.
def _chat(api: str, model: str, text: str, **options) -> str:
    body = {"model": model, "messages": [{"role": "user", "content": text}], **options}
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", headers=_headers(api), json=body, timeout=60)
    return _check(resp).json()["choices"][0]["message"]["content"].strip()


def translate(text: str, target_language: str, script: str = "native") -> str:
    return _chat("translate", "indic-translate", text, target_language=target_language, script=script)


def transliterate(text: str, language: str, script: str = "native") -> str:
    """`language` is the language of the text; `script` is the script you want out."""
    return _chat("transliterate", "indic-transliterate", text, language=language, script=script)


def speak(text: str, voice: str, lang: str, style: str | None = None) -> bytes:
    """Return WAV bytes (PCM16, 24 kHz, mono). Keep `text` to a sentence or two."""
    instructions = {"lang": lang}
    if style:
        instructions["style"] = style
    resp = requests.post(
        f"{BASE_URL}/v1/audio/speech",
        headers=_headers("speak"),
        json={"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instructions)},
        timeout=120,
    )
    return _check(resp).content


# One female voice per language (indic-speak also has male voices).
VOICES = {
    "as": "Prastuti", "bn": "Ishita", "brx": "Gwrbw", "doi": "Preeti", "gu": "Dhara", "hi": "Kavya",
    "kn": "Deepika", "kok": "Anjali", "ks": "Zoon", "mai": "Vaidehi", "ml": "Lakshmi", "mni": "Thoibi",
    "mr": "Anagha", "ne": "Srijana", "or": "Itishree", "pa": "Kaur", "sa": "Bharati", "sat": "Phulmani",
    "sd": "Moomal", "ta": "Anitha", "te": "Sravani", "ur": "Saba",
}


def speak_long(text: str, lang: str, voice: str | None = None, style: str | None = None) -> bytes:
    """indic-speak takes ~30 s of speech per request, so speak sentence by sentence and join the WAVs."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?।॥])\s+", text) if s.strip()]
    with ThreadPoolExecutor(max_workers=3) as pool:  # sentences are independent; keep their order
        clips = list(pool.map(lambda s: speak(s, voice or VOICES[lang], lang, style), sentences))
    frames, params = [], None
    for clip in clips:
        with wave.open(io.BytesIO(clip)) as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))
    return out.getvalue()
