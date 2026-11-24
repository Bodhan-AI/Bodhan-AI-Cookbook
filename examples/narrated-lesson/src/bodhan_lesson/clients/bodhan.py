"""Indic-Translate and Indic-Speak clients. Responses are cached on disk by content hash, so a
re-run (or a retry after a crash) never pays twice for the same text."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from bodhan_lesson import config
from bodhan_lesson.utils.retry import backoff_delay

log = logging.getLogger(__name__)


class BodhanError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TruncatedTranslation(BodhanError):
    """The translation hit its output budget and is incomplete."""


def _cache_path(cache_dir: Path | None, kind: str, parts: dict[str, Any], suffix: str) -> Path | None:
    if cache_dir is None:
        return None
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]
    return cache_dir / kind / f"{digest}{suffix}"


def _write_atomic(path: Path, data: bytes) -> None:
    """Best-effort cache write via a temp file and rename, so a concurrent reader never sees a
    half-written entry. A failed write (e.g. Windows refusing a rename over an open file) only
    costs a future cache miss, never the run."""
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError as exc:
        log.debug("cache write skipped for %s: %s", path.name, exc)
        tmp.unlink(missing_ok=True)


def _read_json_cache(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # unreadable entry: treat as a miss and recompute


class BodhanClient:
    def __init__(self, keys: config.BodhanKeys, *, cache_dir: Path | None = None,
                 http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._keys = keys
        self._cache_dir = cache_dir
        self._http = http or httpx.Client(timeout=config.BODHAN_TIMEOUT_S)
        self._sleep = sleep
        self.translate_calls = 0
        self.speak_calls = 0
        self.transcribe_calls = 0

    @property
    def can_transcribe(self) -> bool:
        return bool(self._keys.transcribe)

    # ---- translate ------------------------------------------------------------------
    def translate(self, text: str, target_language: str, *, script: str = "native") -> str:
        """English text -> `target_language` (an ISO code such as "hi"). Raises
        TruncatedTranslation if the output budget ran out, so the caller can split the input."""
        cache = _cache_path(self._cache_dir, "translate",
                            {"t": text, "l": target_language, "s": script}, ".json")
        if (hit := _read_json_cache(cache)) is not None:
            return hit["text"]
        payload = {"model": "indic-translate", "messages": [{"role": "user", "content": text}],
                   "target_language": target_language, "script": script,
                   "max_output_tokens": config.TRANSLATE_MAX_OUTPUT_TOKENS}
        data = self._post("/chat/completions", self._keys.translate, payload).json()
        try:
            choice = data["choices"][0]
            translated = choice["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as exc:
            raise BodhanError(f"unexpected translate response: {str(data)[:200]}") from exc
        if choice.get("finish_reason") == "length":
            raise TruncatedTranslation("translation reached max_output_tokens")
        if not translated:
            raise BodhanError("empty translation")
        self.translate_calls += 1
        if cache:
            _write_atomic(cache, json.dumps({"text": translated}, ensure_ascii=False).encode("utf-8"))
        return translated

    # ---- speak ----------------------------------------------------------------------
    def speak(self, text: str, voice: str, language: str) -> bytes:
        """One or two sentences -> WAV bytes (PCM16, 24 kHz, mono)."""
        cache = _cache_path(self._cache_dir, "speak", {"t": text, "v": voice, "l": language}, ".wav")
        if cache and cache.is_file():
            try:
                return cache.read_bytes()
            except OSError:
                pass  # being replaced by another thread: fetch again
        payload = {"model": "indic-speak", "input": text, "voice": voice,
                   "instructions": json.dumps({"lang": language, "style": config.TTS_STYLE})}
        audio = self._post("/audio/speech", self._keys.speak, payload).content
        if not audio.startswith(b"RIFF"):
            raise BodhanError("speech response is not a WAV file")
        self.speak_calls += 1
        if cache:
            _write_atomic(cache, audio)
        return audio

    # ---- transcribe -----------------------------------------------------------------
    def transcribe(self, wav_bytes: bytes, language: str) -> str:
        """WAV (at most 30 s) -> text in the native script. Used to back-check the narration."""
        if not self._keys.transcribe:
            raise BodhanError("no Indic-Transcribe key configured")
        cache = _cache_path(self._cache_dir, "transcribe",
                            {"a": hashlib.sha256(wav_bytes).hexdigest(), "l": language}, ".json")
        if (hit := _read_json_cache(cache)) is not None:
            return hit["text"]
        data = self._post("/audio/transcriptions", self._keys.transcribe, None,
                          data={"model": "indic-transcribe", "language": language},
                          files={"file": ("chunk.wav", wav_bytes, "audio/wav")}).json()
        if not isinstance(data, dict) or "text" not in data:
            raise BodhanError(f"unexpected transcribe response: {str(data)[:200]}")
        text = str(data["text"]).strip()
        self.transcribe_calls += 1
        if cache:
            _write_atomic(cache, json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"))
        return text

    # ---- shared ---------------------------------------------------------------------
    def _post(self, path: str, key: str, payload: dict[str, Any] | None, **form: Any) -> httpx.Response:
        last = "no attempt made"
        for attempt in range(config.BODHAN_MAX_ATTEMPTS):
            try:
                response = self._http.post(f"{config.BODHAN_BASE_URL}{path}",
                                           headers={"Authorization": f"Bearer {key}"},
                                           **({"json": payload} if payload is not None else form))
            except httpx.TransportError as exc:
                last = type(exc).__name__
            else:
                if response.status_code == 200:
                    return response
                last = f"HTTP {response.status_code}: {response.text[:200]}"
                if "budget_exceeded" in response.text:
                    raise BodhanError(f"account budget exhausted ({last})", status=429)  # retrying cannot help
                if response.status_code not in (408, 429) and response.status_code < 500:
                    raise BodhanError(f"request rejected ({last})", status=response.status_code)
            log.warning("Bodhan %s failed (%s); retrying", path, last)
            self._sleep(backoff_delay(attempt, 1.0, 30.0))
        raise BodhanError(f"Bodhan {path} unavailable after {config.BODHAN_MAX_ATTEMPTS} attempts: {last}")
