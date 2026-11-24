"""The only place that talks to Bodhan over HTTP.

Every tool goes through BodhanClient, so retries, error conversion, cost tracking,
the budget check and the cache are written once.
"""

from __future__ import annotations

import base64
import json
import random
import threading
import time
import wave
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from bodhan_mcp import costs
from bodhan_mcp.cache import DiskCache, make_key
from bodhan_mcp.config import Settings
from bodhan_mcp.errors import BodhanError

AUTH_RETRY_WINDOW_S = 60  # an auth error right after a success is treated as transient


def _safe_json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return r.text[:300]


def _body(r: httpx.Response, model: str) -> dict[str, Any]:
    """Parse a successful response. A 200 whose body is not a JSON object is reported
    as a structured error instead of crashing later with a TypeError."""
    try:
        d = r.json()
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise BodhanError(
            "bad_response",
            f"{model} returned HTTP {r.status_code} with an unexpected body: {r.text[:200]!r}",
            status=r.status_code, model=model,
            hint="Retry once. If it repeats, check the input (size, format) and the API status.",
        )
    return d


def _dig(d: Any, model: str, *path: Any) -> Any:
    """Follow d[path[0]][path[1]]... or raise a structured error naming the missing part."""
    cur = d
    for key in path:
        try:
            cur = cur[key]
        except (KeyError, IndexError, TypeError):
            raise BodhanError(
                "bad_response", f"{model} response is missing '{'/'.join(map(str, path))}'.", model=model
            ) from None
    return cur


def wav_duration_s(data: bytes) -> float | None:
    try:
        with wave.open(BytesIO(data)) as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError):
        return None

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def image_mime(data: bytes, name: str) -> str:
    """Identify an image by its first bytes (not its extension). Empty or unreadable files
    are rejected here, before any money is spent."""
    if not data:
        raise BodhanError("empty_file", f"{name} is empty (0 bytes).",
                          hint="Re-save or re-export the image, then try again.")
    if data.startswith(PNG_MAGIC):
        return "image/png"
    if data.startswith(JPEG_MAGIC):
        return "image/jpeg"
    raise BodhanError("unsupported_image", f"{name} is not a valid PNG or JPEG image.",
                      hint="Convert the image to PNG or JPEG.")
    
class BodhanClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        tracker: costs.CostTracker | None = None,
        cache: DiskCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
        concurrency: int = 4,
    ) -> None:
        self.settings = settings
        self.http = http or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
        self.tracker = tracker or costs.CostTracker()
        self.cache = cache or DiskCache(settings.cache_dir)
        self._sleep = sleep
        self.max_attempts = max_attempts
        self._sem = threading.BoundedSemaphore(concurrency)  # caps parallel calls
        self._last_ok: dict[str, float] = {}
        self._translate_route = "translate"  # becomes "chat" if /translate returns 404

    # ---- low level -------------------------------------------------------
    def _request(
        self,
        model: str,
        path: str,
        *,
        json_body: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        max_attempts: int | None = None,
    ) -> httpx.Response:
        url = self.settings.base_url + path
        attempts = max_attempts or self.max_attempts
        for n in range(1, attempts + 1):
            headers = {"Authorization": f"Bearer {self.settings.key_for(model)}"}
            retry_after: float | None = None
            try:
                with self._sem:
                    r = self.http.post(url, headers=headers, json=json_body, files=files, data=data)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                err = BodhanError(
                    "network_error", str(e) or type(e).__name__, model=model, retryable=True
                )
            else:
                if r.is_success:
                    self._last_ok[model] = time.monotonic()
                    return r
                err = BodhanError.from_response(
                    model, r.status_code, _safe_json(r), r.headers.get("x-request-id")
                )
                ra = r.headers.get("retry-after")
                retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
                if err.code == "invalid_api_key" and self._recently_ok(model):
                    err.retryable = True
            if not err.retryable or n == attempts:
                raise err
            self._sleep(self._backoff(n, retry_after))
        raise AssertionError("unreachable")

    def _recently_ok(self, model: str) -> bool:
        t = self._last_ok.get(model)
        return t is not None and time.monotonic() - t < AUTH_RETRY_WINDOW_S

    @staticmethod
    def _backoff(attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, 30.0)
        return min(30.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25)

    # ---- translate -------------------------------------------------------
    def translate(self, text: str, target_language: str, script: str = "native") -> dict[str, Any]:
        key = make_key("translate", text, target_language, script)
        hit = self.cache.get_json(key)
        if hit is not None:
            self.tracker.add("translate", 0.0, cached=True)
            return {**hit, "cached": True}

        self.tracker.check("translate", costs.cost_translate(max(1, len(text) // 2)))
        out = None
        if self._translate_route == "translate":
            try:
                body = {"text": text, "target_language": target_language}
                if script != "native":
                    body["script"] = script
                d = _body(self._request("translate", "/translate", json_body=body), "translate")
                out = {
                    "text": _dig(d, "translate", "translation"),
                    "truncated": bool(d.get("truncated")),
                    "output_tokens": (d.get("usage") or {}).get("completion_tokens", 0),
                    "route": "translate",
                }
            except BodhanError as e:
                if e.status != 404:
                    raise
                self._translate_route = "chat"  # remember: do not try /translate again
        if out is None:
            body = {
                "model": "indic-translate",
                "messages": [{"role": "user", "content": text}],
                "target_language": target_language,
            }
            if script != "native":
                body["script"] = script
            d = _body(self._request("translate", "/v1/chat/completions", json_body=body), "translate")
            choice = _dig(d, "translate", "choices", 0)
            out = {
                "text": _dig(choice, "translate", "message", "content") or "",
                "truncated": choice.get("finish_reason") == "length",
                "output_tokens": (d.get("usage") or {}).get("completion_tokens", 0),
                "route": "chat",
            }
        inr = costs.cost_translate(out["output_tokens"])
        self.tracker.add("translate", inr, cached=False, tokens=out["output_tokens"])
        self.cache.put_json(key, out)
        return {**out, "cached": False}

    # ---- speak -----------------------------------------------------------
    def speak(self, text: str, voice: str, lang: str, style: str | None = None) -> bytes:
        instructions = {"lang": lang, **({"style": style} if style else {})}
        body = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            # The API wants instructions as a JSON *string*, not an object.
            "instructions": json.dumps(instructions, ensure_ascii=False),
        }
        key = make_key("speak", body)
        hit = self.cache.get_bytes(key)
        if hit is not None:
            self.tracker.add("speak", 0.0, cached=True)
            return hit

        inr = costs.cost_speak(len(text))
        self.tracker.check("speak", inr)
        audio = self._request("speak", "/v1/audio/speech", json_body=body).content
        if not audio.startswith(b"RIFF"):
            raise BodhanError("bad_audio", "Speech API did not return WAV audio.", model="speak")
        self.tracker.add("speak", inr, cached=False, chars=len(text))
        self.cache.put_bytes(key, audio)
        return audio

    # ---- transcribe ------------------------------------------------------
    def transcribe(self, wav_path: Path | str, language: str | None = None) -> dict[str, Any]:
        path = Path(wav_path)
        audio = path.read_bytes()
        key = make_key("transcribe", audio, language)
        hit = self.cache.get_json(key)
        if hit is not None:
            self.tracker.add("transcribe", 0.0, cached=True)
            return {**hit, "cached": True}

        seconds = wav_duration_s(audio) or 30.0  # unknown length: assume the 30 s maximum
        inr = costs.cost_transcribe(seconds)
        self.tracker.check("transcribe", inr)
        data = {"model": "indic-transcribe", **({"language": language} if language else {})}
        files = {"file": (path.name, audio, "audio/wav")}
        d = _body(self._request("transcribe", "/v1/audio/transcriptions", files=files, data=data), "transcribe")
        out = {"text": _dig(d, "transcribe", "text") or "", "seconds": round(seconds, 2)}
        self.tracker.add("transcribe", inr, cached=False, seconds=round(seconds, 2))
        self.cache.put_json(key, out)
        return {**out, "cached": False}

    # ---- ocr -------------------------------------------------------------
    def ocr(
        self, image_path: Path | str, table_format: str = "html", max_tokens: int | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        path = Path(image_path)
        img = path.read_bytes()
        mime = image_mime(img, path.name)
        key = make_key("ocr", img, table_format, max_tokens)
        hit = self.cache.get_json(key)
        if hit is not None:
            self.tracker.add("ocr", 0.0, cached=True)
            return {**hit, "cached": True}

        self.tracker.check("ocr", costs.OCR_PER_IMAGE)
       # mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        body: dict[str, Any] = {
            "model": "indic-ocr",
            "messages": [{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{base64.b64encode(img).decode()}"}}
            ]}],
            "table_format": table_format,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        d = _body(self._request("ocr", "/v1/chat/completions", json_body=body, max_attempts=max_attempts), "ocr")
        out = {
            "text": _dig(d, "ocr", "choices", 0, "message", "content") or "",
            "blocks": d.get("blocks") or [],
            "usage": d.get("usage"),
        }
        self.tracker.add("ocr", costs.OCR_PER_IMAGE, cached=False)
        self.cache.put_json(key, out)
        return {**out, "cached": False}