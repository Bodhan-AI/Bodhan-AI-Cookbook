"""Thin client for the Bodhan hosted API, with retries, a disk cache and cost tracking."""

import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}

# Measured per key: transcription allows 15 requests a minute; speech about 8 (the 9th and 10th
# calls in a minute got HTTP 429 on 2026-11-20).
# Retries count against the limit too, so stay under it rather than retry into it.
REQUESTS_PER_MINUTE = {"transcribe": 15, "translate": 15, "speak": 8}

# Hosted prices in rupees.
INR_PER_AUDIO_MINUTE = 0.10
INR_PER_OUTPUT_TOKEN = 0.20 / 10_000
INR_PER_SPEAK_CHAR = 6 / 10_000


LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "mr": "Marathi", "te": "Telugu", "ta": "Tamil",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "or": "Odia", "pa": "Punjabi", "as": "Assamese", "ur": "Urdu",
}


class BodhanError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class TruncatedError(BodhanError):
    pass


class Bodhan:
    def __init__(self, cache_dir: Path):
        self.base_url = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.cost = {"transcribe": 0.0, "translate": 0.0, "speak": 0.0}
        self.calls = {"transcribe": 0, "translate": 0, "speak": 0, "cached": 0, "retries": 0}
        self._lock = threading.Lock()
        self._limiters = {m: RateLimiter(rpm) for m, rpm in REQUESTS_PER_MINUTE.items()}
        self._route = {"translate_via_chat": False}  # set if POST /translate is not found

    def child(self) -> "Bodhan":
        """A client for one language: shares the cache, rate limits and route with this one,
        but counts its own calls and cost, so each language's cost can be reported."""
        c = Bodhan(self.cache)
        c._limiters, c._route = self._limiters, self._route
        return c

    # -- public calls -------------------------------------------------------

    def transcribe(self, wav: Path, language: str, seconds: float) -> str:
        audio = wav.read_bytes()
        key = _hash("transcribe", language, audio)

        def call():
            r = self._post(
                "/v1/audio/transcriptions",
                "transcribe",
                files={"file": (wav.name, audio, "audio/wav")},
                data={"model": "indic-transcribe", "language": language},
            )
            self._spend("transcribe", seconds / 60 * INR_PER_AUDIO_MINUTE)
            return r.json()["text"]

        return self._cached_json(key, call)

    def translate(self, text: str, target: str, script: str = "native") -> str:
        """Prefer POST /translate, as the docs do. If the route is missing (HTTP 404, seen on
        2026-11-19), use the documented chat transport, which gives identical output."""
        key = _hash("translate-v2", target, script, text.encode())

        def call():
            if not self._route["translate_via_chat"]:
                try:
                    body = self._post(
                        "/translate",
                        "translate",
                        json={"text": text, "target_language": target, "script": script},
                    ).json()
                except BodhanError as e:
                    if e.status != 404:
                        raise
                    log.warning("POST /translate not found; using /v1/chat/completions instead")
                    self._route["translate_via_chat"] = True
                else:
                    if body.get("truncated"):
                        raise TruncatedError(f"translation truncated ({target}): {text[:60]!r}")
                    self._spend("translate", body["usage"]["completion_tokens"] * INR_PER_OUTPUT_TOKEN)
                    return body["translation"]

            # The prompt format from the Indic-Translate model card: a blank line before the text,
            # and "(code-mixed)" or "(Roman)" after the language name for those scripts.
            suffix = {"codemix": " (code-mixed)", "roman": " (Roman)"}.get(script, "")
            prompt = f"Translate the following text into {LANGUAGE_NAMES[target]}{suffix}:\n\n{text}"
            body = self._post(
                "/v1/chat/completions",
                "translate",
                json={"model": "indic-translate", "messages": [{"role": "user", "content": prompt}]},
            ).json()
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise TruncatedError(f"translation truncated ({target}): {text[:60]!r}")
            self._spend("translate", body["usage"]["completion_tokens"] * INR_PER_OUTPUT_TOKEN)
            return choice["message"]["content"].strip()

        return self._cached_json(key, call)

    def speak(self, text: str, lang: str, voice: str, out: Path, attempt: int = 0) -> Path:
        """Indic-Speak is not deterministic, so `attempt` gives a retry its own cache entry."""
        instructions = json.dumps({"lang": lang})
        key = _hash("speak", voice, instructions, attempt, text.encode())
        cached = self.cache / f"{key}.wav"
        if cached.exists():
            self._count("cached")
        else:
            r = self._post(
                "/v1/audio/speech",
                "speak",
                json={"model": "indic-speak", "input": text, "voice": voice, "instructions": instructions},
            )
            cached.write_bytes(r.content)
            self._spend("speak", len(text) * INR_PER_SPEAK_CHAR)
        out.write_bytes(cached.read_bytes())
        return out

    # -- plumbing -------------------------------------------------------------

    def _post(self, path: str, model: str, **kwargs) -> requests.Response:
        key_env = f"BODHAN_{model.upper()}_KEY"
        api_key = os.environ.get(key_env) or os.environ.get("BODHAN_API_KEY")
        if not api_key:
            raise BodhanError(f"set {key_env} (or BODHAN_API_KEY) in .env")
        headers = {"Authorization": f"Bearer {api_key}"}

        for attempt in range(5):
            self._limiters[model].wait()
            try:
                r = requests.post(self.base_url + path, headers=headers, timeout=120, **kwargs)
            except requests.RequestException as e:
                problem = str(e)
            else:
                if r.ok:
                    self._count(model)
                    return r
                problem = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code not in RETRY_STATUS:
                    raise BodhanError(f"{path} {problem}", r.status_code)
            wait = 2**attempt
            log.warning("%s failed (%s), retry in %ss", path, problem, wait)
            self._count("retries")
            time.sleep(wait)
        raise BodhanError(f"{path} still failing after retries: {problem}")

    def _cached_json(self, key: str, call):
        path = self.cache / f"{key}.json"
        if path.exists():
            self._count("cached")
            return json.loads(path.read_text())
        value = call()
        path.write_text(json.dumps(value, ensure_ascii=False))
        return value

    def _spend(self, model: str, inr: float) -> None:
        with self._lock:
            self.cost[model] += inr

    def _count(self, name: str) -> None:
        with self._lock:
            self.calls[name] = self.calls.get(name, 0) + 1


class RateLimiter:
    """Blocks so that at most `per_minute` calls start in any 60-second window."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.started = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            while len(self.started) >= self.per_minute:
                wait = 60.5 - (time.monotonic() - self.started[0])
                if wait > 0:
                    time.sleep(wait)
                self.started.popleft()
            self.started.append(time.monotonic())


def _hash(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:32]
