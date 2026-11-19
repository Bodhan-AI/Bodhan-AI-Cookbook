"""Async client for Bodhan's four models, with disk cache, rupee budget and retries.

Every paid call goes through `Bodhan._call`: validate -> cache -> reserve budget -> HTTP
with retries -> settle cost -> store in cache. Endpoint shapes were checked against the
live API (translation is served only on /v1/chat/completions; TTS returns MP3).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import random
from pathlib import Path

import httpx

from . import languages as L

BASE_URL = "https://api.bodhan.ai"
CACHE_DIR = Path.home() / ".cache" / "bodhan_agent"
KEY_ENV = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}
# Rupees per billed unit: audio second / output token / input character / image.
PRICE_INR = {"indic-transcribe": 0.10 / 60, "indic-translate": 0.20 / 10_000, "indic-speak": 6 / 10_000, "indic-ocr": 0.20}
AUDIO_MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
RETRY_STATUS = {429, 500, 502, 503, 504}


def load_env(path: Path = Path(".env")) -> None:
    """Read KEY=value lines from .env into the environment (real env vars win)."""
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


class BodhanError(Exception):
    """`code` is stable for branching; `hint` says what to do next."""

    def __init__(self, code: str, message: str, hint: str = "", retryable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.hint, self.retryable = code, message, hint, retryable

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint, "retryable": self.retryable}


class Budget:
    """Rupee budget for one run. Calls reserve their estimate before sending, so parallel
    calls can't jointly overshoot (no await between check and reserve, so no lock needed)."""

    def __init__(self, limit_inr: float = math.inf):
        self.limit_inr, self.spent_inr, self.calls = limit_inr, 0.0, []

    def reserve(self, model: str, inr: float) -> None:
        if self.spent_inr + inr > self.limit_inr + 1e-9:
            raise BodhanError("budget_exceeded", f"{model} needs ~Rs {inr:.4f}, only Rs "
                              f"{self.limit_inr - self.spent_inr:.4f} left", "Raise --max-cost-inr or shorten the input.")
        self.spent_inr += inr

    def settle(self, model: str, reserved: float, actual: float, cached: bool = False) -> None:
        self.spent_inr += actual - reserved
        self.calls.append({"model": model, "cost_inr": round(actual, 6), "cached": cached})


def cache_key(model: str, params: dict, data: bytes) -> str:
    """Same model + settings + input = same key. The API key is deliberately not part of it."""
    head = json.dumps([model, params], sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(head + b"\0" + data).hexdigest()


def _unsupported(what: str, code: str, allowed: frozenset) -> BodhanError:
    return BodhanError("unsupported_language", f"{L.NAMES.get(code, code)} ({code}) is not supported for {what}",
                       f"Supported: {', '.join(sorted(allowed))}")


class Bodhan:
    def __init__(self, *, budget: Budget | None = None, cache_dir: Path = CACHE_DIR, timeout_s: float = 60,
                 retries: int = 3, backoff_s: float = 1.0, concurrency: int = 4, transport=None):
        self.budget = budget or Budget()
        self.cache_dir = Path(cache_dir)
        self.retries, self.backoff_s = retries, backoff_s
        self.sem = asyncio.Semaphore(concurrency)  # server allows 10 in flight per key
        self.http = httpx.AsyncClient(base_url=BASE_URL, timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self.http.aclose()

    # --- one method per model ---------------------------------------------------------

    async def transcribe(self, audio: bytes, filename: str, duration_s: float, language: str | None = None) -> str:
        """One audio window (<= 30 s) -> text. language=None lets the API auto-detect."""
        if language and language not in L.STT_LANGS:
            raise _unsupported("transcription", language, L.STT_LANGS)
        mime = AUDIO_MIME.get(Path(filename).suffix.lower())
        if not mime:
            raise BodhanError("unsupported_media", f"{filename}: not WAV/MP3/FLAC/OGG", "Convert with ffmpeg first.")
        form = {"model": "indic-transcribe"} | ({"language": language} if language else {})
        req = {"url": "/v1/audio/transcriptions", "data": form, "files": {"file": (filename, audio, mime)}}
        est = PRICE_INR["indic-transcribe"] * duration_s
        out = await self._call("indic-transcribe", {"language": language}, audio, est, req,
                               parse=lambda r: {"text": r.json()["text"]})
        return out["text"]

    async def translate(self, text: str, target: str) -> str:
        """Text -> target language. The API detects the source language itself."""
        if target not in L.TRANSLATE_LANGS:
            raise _unsupported("translation", target, L.TRANSLATE_LANGS)
        if not text.strip():
            raise BodhanError("bad_request", "nothing to translate")
        body = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], "target_language": target}
        est = PRICE_INR["indic-translate"] * len(text)  # upper bound: < 1 output token per input char

        def parse(r: httpx.Response) -> dict:
            j = r.json()
            if j["choices"][0].get("finish_reason") == "length":
                raise BodhanError("truncated", "translation hit the token limit", "Split the text into smaller chunks.")
            return {"text": j["choices"][0]["message"]["content"], "tokens": j["usage"]["completion_tokens"]}

        out = await self._call("indic-translate", {"target": target}, text.encode(), est,
                               {"url": "/v1/chat/completions", "json": body}, parse=parse,
                               actual=lambda o: PRICE_INR["indic-translate"] * o["tokens"])
        return out["text"]

    async def speak(self, text: str, voice: str, lang: str, style: str | None = None, attempt: int = 0) -> bytes:
        """One short text (a sentence or two) -> MP3 bytes. attempt > 0 asks for a fresh take (not the cached one)."""
        if lang not in L.TTS_LANGS:
            raise _unsupported("speech", lang, L.TTS_LANGS)
        if voice not in L.ALL_VOICES:  # the API answers a bad voice with a bare 500, so check here
            raise BodhanError("bad_request", f"unknown voice {voice!r}",
                              f"e.g. {L.pick_voice(lang, 'female')} (female) or {L.pick_voice(lang, 'male')} (male)")
        if style and style not in L.STYLES:
            raise BodhanError("bad_request", f"unknown style {style!r}", f"Use one of {sorted(L.STYLES)} or omit it.")
        if not text.strip():
            raise BodhanError("bad_request", "nothing to speak")
        instructions = {"lang": lang} | ({"style": style} if style else {})
        body = {"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instructions)}

        def parse(r: httpx.Response) -> bytes:
            if not r.headers.get("content-type", "").startswith("audio/"):
                raise BodhanError("bad_response", f"expected audio, got {r.headers.get('content-type')}")
            return r.content

        params = {"voice": voice, **instructions} | ({"attempt": attempt} if attempt else {})
        return await self._call("indic-speak", params, text.encode(),
                                PRICE_INR["indic-speak"] * len(text), {"url": "/v1/audio/speech", "json": body},
                                parse=parse)

    async def ocr(self, image: bytes, filename: str, table_format: str = "markdown") -> str:
        """One page image (PNG/JPEG) -> Markdown."""
        mime = IMAGE_MIME.get(Path(filename).suffix.lower())
        if not mime:
            raise BodhanError("unsupported_media", f"{filename}: OCR takes PNG or JPEG", "Render PDF pages to PNG first.")
        uri = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        body = {"model": "indic-ocr", "table_format": table_format,
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": uri}}]}]}
        out = await self._call("indic-ocr", {"table_format": table_format}, image, PRICE_INR["indic-ocr"],
                               {"url": "/v1/chat/completions", "json": body},
                               parse=lambda r: {"text": r.json()["choices"][0]["message"]["content"]})
        return out["text"]

    # --- shared machinery -------------------------------------------------------------

    async def _call(self, model, params, data, est, req, *, parse, actual=None):
        path = self.cache_dir / cache_key(model, params, data)
        if path.exists():
            self.budget.settle(model, 0, 0, cached=True)
            return path.read_bytes() if model == "indic-speak" else json.loads(path.read_text("utf-8"))
        key = os.environ.get(KEY_ENV[model]) or os.environ.get("BODHAN_API_KEY")
        if not key:
            raise BodhanError("missing_key", f"no API key for {model}", f"Set {KEY_ENV[model]} in .env.")
        self.budget.reserve(model, est)  # raises before any spend
        try:
            async with self.sem:
                result = parse(await self._send(model, req, key))
        except Exception as e:
            self.budget.spent_inr -= est  # failed calls are not billed
            if isinstance(e, BodhanError):
                raise
            raise BodhanError("bad_response", f"{model} returned an unexpected response: {e!r}") from e
        self.budget.settle(model, est, actual(result) if actual else est)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")  # write then rename: a crash never leaves half an entry
        tmp.write_bytes(result if isinstance(result, bytes) else json.dumps(result, ensure_ascii=False).encode())
        tmp.replace(path)
        return result

    async def _send(self, model: str, req: dict, key: str) -> httpx.Response:
        """Retry 429/5xx/timeouts/network errors with exponential backoff + jitter; raise other 4xx at once."""
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                r = await self.http.post(headers={"Authorization": f"Bearer {key}"}, **req)
            except httpx.TransportError as e:  # includes timeouts
                if last:
                    raise BodhanError("unavailable", f"{model}: {e!r} after {attempt + 1} tries", "Retry later.", True) from e
            else:
                if r.status_code < 400:
                    return r
                if r.status_code not in RETRY_STATUS or last:
                    raise _http_error(model, r)
            await asyncio.sleep(self.backoff_s * 2**attempt + random.uniform(0, self.backoff_s))
        raise AssertionError("unreachable")


def _http_error(model: str, r: httpx.Response) -> BodhanError:
    try:
        msg = str(r.json().get("error", {}).get("message") or r.text)[:500]
    except ValueError:
        msg = r.text[:500]
    s = r.status_code
    if s in (401, 403):
        return BodhanError("auth_failed", f"{model} rejected the key", f"Check {KEY_ENV[model]}; keys are per model.")
    if s in RETRY_STATUS:
        return BodhanError("unavailable", f"{model} HTTP {s} after retries: {msg}", "Retry later.", True)
    if "unsupported language" in msg.lower():
        return BodhanError("unsupported_language", msg)
    return BodhanError("bad_request", f"{model} HTTP {s}: {msg}", "Fix the arguments; retrying won't help.")
