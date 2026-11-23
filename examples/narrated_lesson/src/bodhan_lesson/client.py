"""Thin client for the four Bodhan models and the companion LLM.

Every call goes through one retry wrapper, one disk cache and one cost tracker.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import mimetypes
import os
import random
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import requests
from dotenv import load_dotenv

# Prices in INR, taken from the assignment brief.
PRICE_OCR_PER_PAGE = 0.20
PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_TRANSCRIBE_PER_MINUTE = 0.10

RETRYABLE = {429, 500, 502, 503, 504}


class BodhanError(RuntimeError):
    """A readable API error with the status, code and request id attached."""

    def __init__(self, status, code, message, request_id=None):
        super().__init__(f"{status} {code}: {message} (request_id={request_id})")
        self.status = status
        self.code = code
        self.request_id = request_id

    @classmethod
    def from_response(cls, resp: requests.Response) -> "BodhanError":
        try:
            err = resp.json()["error"]
            return cls(resp.status_code, err.get("code"), err.get("message"), err.get("request_id"))
        except (ValueError, KeyError, TypeError, AttributeError):
            return cls(resp.status_code, None, resp.text[:300])


def wav_seconds(blob: bytes) -> float:
    with contextlib.closing(wave.open(io.BytesIO(blob), "rb")) as w:
        return w.getnframes() / w.getframerate()


class DiskCache:
    """Stores API results on disk, keyed by a hash of the request."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(kind: str, params: dict, blob: bytes | None = None) -> str:
        h = hashlib.sha256()
        h.update(kind.encode())
        h.update(json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        if blob is not None:
            h.update(hashlib.sha256(blob).digest())
        return h.hexdigest()

    def get_json(self, key: str):
        p = self.root / f"{key}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put_json(self, key: str, value) -> None:
        (self.root / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def get_bytes(self, key: str):
        p = self.root / f"{key}.bin"
        return p.read_bytes() if p.exists() else None

    def put_bytes(self, key: str, value: bytes) -> None:
        (self.root / f"{key}.bin").write_bytes(value)


@dataclass
class CostTracker:
    records: list = field(default_factory=list)

    def add(self, service: str, units: float, cost_inr: float, cached: bool, seconds: float) -> None:
        self.records.append(
            {"service": service, "units": units, "cost_inr": cost_inr, "cached": cached, "seconds": seconds}
        )

    def summary(self) -> dict:
        by_service: dict = {}
        for r in self.records:
            s = by_service.setdefault(
                r["service"], {"calls": 0, "cached_calls": 0, "units": 0, "cost_inr": 0.0, "seconds": 0.0}
            )
            s["calls"] += 1
            s["cached_calls"] += int(r["cached"])
            s["units"] += r["units"]
            s["cost_inr"] += r["cost_inr"]
            s["seconds"] += r["seconds"]
        total = round(sum(s["cost_inr"] for s in by_service.values()), 5)
        return {"by_service": by_service, "total_inr": total}


class BodhanClient:
    def __init__(self, cache_dir: str | Path | None = None, base_url: str | None = None):
        load_dotenv()
        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or "https://api.bodhan.ai").rstrip("/")
        self.cache = DiskCache(cache_dir or os.environ.get("LESSON_CACHE_DIR", ".cache"))
        self.costs = CostTracker()

    # ---- plumbing -------------------------------------------------------

    @staticmethod
    def _env(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise RuntimeError(f"Missing environment variable {name}. Copy .env.example to .env and fill it in.")
        return value

    @staticmethod
    def _backoff(attempt: int, status: int | None = None) -> float:
        base = 5 if status == 429 else 1          # rate limits need longer waits than a blip
        return min(60.0, base * 2 ** attempt) + random.random()

    @staticmethod
    def _retry_after(resp: requests.Response) -> float:
        try:
            return float(resp.headers.get("Retry-After", ""))
        except ValueError:
            return 0.0

    def _send(self, url: str, key: str, *, retries: int = 6, **kw) -> requests.Response:
        """POST with backoff on 429 and 5xx. Raises BodhanError on final failure.

        A 'truncated' recognizer error is retried at most twice: the same request has both failed
        and succeeded in practice, but it can also fail every time.
        """
        timeout = kw.pop("timeout", 180)
        for attempt in range(retries + 1):
            last = attempt == retries
            try:
                resp = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout, **kw)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if last:
                    raise BodhanError(None, "network", str(exc)) from exc
                time.sleep(self._backoff(attempt))
                continue
            truncated = "truncated" in resp.text.lower()
            retryable = resp.status_code in RETRYABLE and (not truncated or attempt < 2)
            if retryable and not last:
                time.sleep(min(self._retry_after(resp) or self._backoff(attempt, resp.status_code), 60))
                continue
            if not resp.ok:
                raise BodhanError.from_response(resp)
            return resp
        raise BodhanError(None, "retries_exhausted", url)  # unreachable, keeps type checkers happy

    # ---- OCR ------------------------------------------------------------

    def ocr_page(self, image_path: str | Path, table_format: str = "markdown", max_tokens: int | None = None) -> dict:
        """OCR one page image. Returns {"markdown": str, "blocks": list}."""
        path = Path(image_path)
        blob = path.read_bytes()
        key = self.cache.make_key("ocr", {"table_format": table_format, "max_tokens": max_tokens}, blob)
        cached = self.cache.get_json(key)
        if cached is not None:
            self.costs.add("ocr", 1, 0.0, True, 0.0)
            return cached

        start = time.time()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        b64 = base64.b64encode(blob).decode()
        body = {
            "model": "indic-ocr",
            "table_format": table_format,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        data = self._send(f"{self.base_url}/v1/chat/completions", self._env("BODHAN_OCR_KEY"), json=body).json()
        result = {"markdown": data["choices"][0]["message"]["content"], "blocks": data.get("blocks", [])}
        self.cache.put_json(key, result)
        self.costs.add("ocr", 1, PRICE_OCR_PER_PAGE, False, time.time() - start)
        return result

    # ---- translation ----------------------------------------------------

    def translate(self, text: str, target_language: str) -> dict:
        """Translate text. Returns {"text", "finish_reason", "truncated"}."""
        key = self.cache.make_key("translate", {"text": text, "lang": target_language})
        cached = self.cache.get_json(key)
        if cached is not None:
            self.costs.add("translate", 0, 0.0, True, 0.0)
            return cached

        start = time.time()
        body = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "target_language_code": target_language,
        }
        data = self._send(f"{self.base_url}/v1/chat/completions", self._env("BODHAN_TRANSLATE_KEY"), json=body).json()
        choice = data["choices"][0]
        result = {
            "text": choice["message"]["content"],
            "finish_reason": choice["finish_reason"],
            "truncated": choice["finish_reason"] == "length",
        }
        tokens = data.get("usage", {}).get("completion_tokens", 0)
        self.cache.put_json(key, result)
        self.costs.add("translate", tokens, tokens / 10_000 * PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS, False, time.time() - start)
        return result

    # ---- speech ---------------------------------------------------------

    def speak(self, text: str, voice: str, lang: str, style: str | None = None) -> bytes:
        """Synthesize speech. Returns WAV bytes (PCM16, 24 kHz, mono)."""
        key = self.cache.make_key("speak", {"text": text, "voice": voice, "lang": lang, "style": style})
        cached = self.cache.get_bytes(key)
        if cached is not None:
            self.costs.add("speak", len(text), 0.0, True, 0.0)
            return cached

        start = time.time()
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        body = {"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instructions)}
        audio = self._send(f"{self.base_url}/v1/audio/speech", self._env("BODHAN_SPEAK_KEY"), json=body).content
        self.cache.put_bytes(key, audio)
        self.costs.add("speak", len(text), len(text) / 10_000 * PRICE_SPEAK_PER_10K_CHARS, False, time.time() - start)
        return audio

    def transcribe(self, wav_bytes: bytes, language: str | None = None) -> str:
        """Transcribe a short WAV clip (about 30 seconds or less)."""
        key = self.cache.make_key("transcribe", {"language": language}, wav_bytes)
        cached = self.cache.get_json(key)
        minutes = wav_seconds(wav_bytes) / 60
        if cached is not None:
            self.costs.add("transcribe", minutes, 0.0, True, 0.0)
            return cached["text"]

        start = time.time()
        data = {"model": "indic-transcribe"}
        if language:
            data["language"] = language
        resp = self._send(
            f"{self.base_url}/v1/audio/transcriptions",
            self._env("BODHAN_TRANSCRIBE_KEY"),
            files={"file": ("clip.wav", wav_bytes, "audio/wav")},
            data=data,
        )
        text = resp.json()["text"]
        self.cache.put_json(key, {"text": text})
        self.costs.add("transcribe", minutes, minutes * PRICE_TRANSCRIBE_PER_MINUTE, False, time.time() - start)
        return text

    # ---- companion LLM --------------------------------------------------

    def llm_chat(self, messages: list[dict], max_tokens: int = 2000, temperature: float = 0.0) -> str:
        """Plain OpenAI-style chat completion against LLM_BASE_URL. English text only."""
        model = self._env("LLM_MODEL")
        key = self.cache.make_key("llm", {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature})
        cached = self.cache.get_json(key)
        if cached is not None:
            self.costs.add("llm", 0, 0.0, True, 0.0)
            return cached["text"]

        start = time.time()
        url = self._env("LLM_BASE_URL").rstrip("/") + "/chat/completions"
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        data = self._send(url, self._env("LLM_API_KEY"), json=body).json()
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        if not text:
            raise RuntimeError(f"LLM returned empty content (finish_reason={choice.get('finish_reason')}). Try a larger max_tokens.")
        self.cache.put_json(key, {"text": text})
        self.costs.add("llm", data.get("usage", {}).get("total_tokens", 0), 0.0, False, time.time() - start)
        return text