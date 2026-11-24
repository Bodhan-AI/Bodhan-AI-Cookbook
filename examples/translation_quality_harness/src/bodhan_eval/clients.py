"""Bodhan and LLM clients: caching, retries with backoff, a concurrency limit and a rupee budget.

Every paid call goes through ``_cached``: a cache hit is free and never touches the network,
a miss is checked against the budget first, then retried on 429/5xx/timeouts.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field

import httpx
import numpy as np
import soundfile as sf

from bodhan_eval.cache import Cache, cache_key
from bodhan_eval.langs import VOICES, speech_lang

log = logging.getLogger("bodhan_eval.clients")

# Hosted prices from the assignment (INR).
PRICE_TRANSLATE_PER_TOKEN = 0.20 / 10_000
PRICE_SPEAK_PER_CHAR = 6.0 / 10_000
PRICE_TRANSCRIBE_PER_SEC = 0.10 / 60

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_TRANSCRIBE_SEC = 30.0


class BudgetExceeded(RuntimeError):
    pass


class ModelError(RuntimeError):
    """A model call failed for good (bad input, auth, or retries exhausted)."""


@dataclass
class Budget:
    """Tracks rupees spent on network calls this run and refuses calls that would exceed the cap."""

    max_inr: float | None
    spent: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self, model: str, estimate: float) -> None:
        with self._lock:
            if self.max_inr is not None and self.spent + estimate > self.max_inr + 1e-9:
                raise BudgetExceeded(f"{model} call (~₹{estimate:.4f}) would exceed --max-cost-inr {self.max_inr} (spent ₹{self.spent:.4f})")
            self.spent += estimate
            self.by_model[model] = self.by_model.get(model, 0.0) + estimate
            self.calls[model] = self.calls.get(model, 0) + 1

    def settle(self, model: str, estimate: float, actual: float) -> None:
        with self._lock:
            self.spent += actual - estimate
            self.by_model[model] = self.by_model.get(model, 0.0) + actual - estimate


def _key(model_env: str) -> str | None:
    for name in (model_env, "BODHAN_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value and not value.startswith("<"):  # ignore unfilled .env.example placeholders
            return value
    return None


def _retry(fn, *, attempts: int = 5, rate_limit_attempts: int = 12, what: str):
    """Retry transient failures with exponential backoff. Rate limits (429) are temporary by definition,
    so they get more attempts and honour retry-after (seen live: 17-55 s waits for minutes on end)."""
    delay = 1.0
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            limit = rate_limit_attempts if status == 429 else attempts
            if status not in RETRYABLE or attempt >= limit:
                raise ModelError(f"{what}: HTTP {status}: {e.response.text[:300]}") from e
            retry_after = e.response.headers.get("retry-after")
            wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else delay
            reason = f"HTTP {status}"
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt >= attempts:
                raise ModelError(f"{what}: {type(e).__name__}: {e}") from e
            wait, reason = delay, type(e).__name__
        log.warning("retrying %s after %s (attempt %d, waiting %.1fs)", what, reason, attempt, wait,
                    extra={"what": what, "reason": reason, "attempt": attempt, "wait_s": round(wait, 2)})
        time.sleep(wait + random.uniform(0, 0.5))
        delay = min(delay * 2, 30.0)


def wav_duration(data: bytes) -> float:
    info = sf.info(io.BytesIO(data))
    return info.frames / info.samplerate


class Bodhan:
    """Indic-Translate, Indic-Speak and Indic-Transcribe over the hosted API."""

    def __init__(self, cache: Cache, budget: Budget, *, base_url: str | None = None, concurrency: int = 4, timeout: float = 60.0):
        self.cache = cache
        self.budget = budget
        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or "https://api.bodhan.ai").rstrip("/")
        self.http = httpx.Client(timeout=timeout)
        self.sem = threading.Semaphore(concurrency)
        self._translate_via_chat = False  # set once /translate 404s (gateways that only expose the /v1 routes)

    def _post(self, path: str, key_env: str, **kwargs) -> httpx.Response:
        key = _key(key_env)
        if not key:
            raise ModelError(f"{key_env} (or BODHAN_API_KEY) is not set")

        def call():
            with self.sem:
                r = self.http.post(f"{self.base_url}{path}", headers={"Authorization": f"Bearer {key}"}, **kwargs)
            r.raise_for_status()
            return r

        return _retry(call, what=path)

    def translate(self, text: str, target: str) -> dict:
        """Returns {"translation", "truncated", "tokens"}. Cached by (text, target)."""
        key = cache_key("translate", {"text": text, "target": target})
        if (hit := self.cache.get(key)) is not None:
            return hit
        estimate = max(1, len(text)) * 1.5 * PRICE_TRANSLATE_PER_TOKEN  # generous: Indic output ~ 1–1.5 tokens/char
        self.budget.reserve("indic-translate", estimate)
        try:
            result = self._translate_call(text, target)
        except Exception:
            self.budget.settle("indic-translate", estimate, 0.0)
            raise
        self.budget.settle("indic-translate", estimate, result["tokens"] * PRICE_TRANSLATE_PER_TOKEN)
        self.cache.put(key, result)
        return result

    def _translate_call(self, text: str, target: str) -> dict:
        """POST /translate (documented route); on 404 switch to the OpenAI-style chat route, which takes
        one user turn plus a ``target_language`` field and reports truncation as finish_reason == "length"."""
        if not self._translate_via_chat:
            try:
                body = self._post("/translate", "BODHAN_TRANSLATE_KEY", json={"text": text, "target_language": target}).json()
                tokens = int(body.get("usage", {}).get("completion_tokens", 0))
                return {"translation": body["translation"], "truncated": bool(body.get("truncated")), "tokens": tokens}
            except ModelError as e:
                if "HTTP 404" not in str(e):
                    raise
                log.warning("/translate not served; using /v1/chat/completions for indic-translate")
                self._translate_via_chat = True
        body = self._post("/v1/chat/completions", "BODHAN_TRANSLATE_KEY", json={
            "model": "indic-translate", "messages": [{"role": "user", "content": text}], "target_language": target,
        }).json()
        choice = body["choices"][0]
        return {
            "translation": (choice["message"].get("content") or "").strip(),
            "truncated": choice.get("finish_reason") == "length",
            "tokens": int(body.get("usage", {}).get("completion_tokens", 0)),
        }

    def speak(self, text: str, lang: str, voice: str | None = None, style: str | None = None) -> tuple[bytes, float]:
        """Synthesize one short utterance. Returns (wav bytes, cost in INR)."""
        lang = speech_lang(lang)
        voice = voice or VOICES.get(lang, "Kavya")
        instructions = {"lang": lang, **({"style": style} if style else {})}
        key = cache_key("speak", {"text": text, "lang": lang, "voice": voice, **({"style": style} if style else {})})
        path = self.cache.blob_path(key)
        cost = len(text) * PRICE_SPEAK_PER_CHAR
        if path.exists():
            return path.read_bytes(), cost
        self.budget.reserve("indic-speak", cost)
        try:
            r = self._post("/v1/audio/speech", "BODHAN_SPEAK_KEY", json={
                "model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instructions),
            })
        except Exception:
            self.budget.settle("indic-speak", cost, 0.0)
            raise
        self.cache.put_blob(key, r.content)
        return r.content, cost

    def transcribe(self, wav: bytes, lang: str) -> dict:
        """Transcribe audio of any length by splitting it into <=30 s windows at quiet points."""
        lang = speech_lang(lang)
        texts, cost = [], 0.0
        for chunk in split_for_asr(wav):
            out = self._transcribe_window(chunk, lang)
            texts.append(out["text"])
            cost += out["cost"]
        return {"text": " ".join(t.strip() for t in texts if t.strip()), "cost": cost}

    def _transcribe_window(self, wav: bytes, lang: str) -> dict:
        key = cache_key("transcribe", {"audio_sha": hashlib.sha256(wav).hexdigest(), "lang": lang})
        if (hit := self.cache.get(key)) is not None:
            return hit
        cost = wav_duration(wav) * PRICE_TRANSCRIBE_PER_SEC
        self.budget.reserve("indic-transcribe", cost)
        try:
            body = self._post("/v1/audio/transcriptions", "BODHAN_TRANSCRIBE_KEY",
                              data={"model": "indic-transcribe", "language": lang},
                              files={"file": ("audio.wav", wav, "audio/wav")}).json()
        except Exception:
            self.budget.settle("indic-transcribe", cost, 0.0)
            raise
        result = {"text": body.get("text", ""), "cost": cost}
        self.cache.put(key, result)
        return result


def split_for_asr(wav: bytes, max_sec: float = MAX_TRANSCRIBE_SEC - 2) -> list[bytes]:
    """Split audio into windows below the ASR limit, cutting at the quietest 20 ms frame
    in the last third of each window so words are not broken."""
    samples, sr = sf.read(io.BytesIO(wav), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    if len(mono) <= max_sec * sr:
        return [wav]
    frame = int(0.02 * sr)
    chunks, start = [], 0
    while len(mono) - start > max_sec * sr:
        lo, hi = start + int(max_sec * sr * 2 / 3), start + int(max_sec * sr)
        energy = [float(np.mean(mono[i:i + frame] ** 2)) for i in range(lo, hi - frame, frame)]
        cut = lo + int(np.argmin(energy)) * frame
        chunks.append(mono[start:cut])
        start = cut
    chunks.append(mono[start:])
    out = []
    for c in chunks:
        buf = io.BytesIO()
        sf.write(buf, c, sr, format="WAV", subtype="PCM_16")
        out.append(buf.getvalue())
    return out


class LLM:
    """OpenAI-compatible chat client for the judge. Reads LLM_BASE_URL, LLM_MODEL, LLM_API_KEY."""

    def __init__(self, cache: Cache, *, concurrency: int = 4, timeout: float = 120.0):
        from openai import OpenAI

        self.model = os.environ.get("LLM_MODEL")
        if not self.model or not os.environ.get("LLM_API_KEY"):
            raise ModelError("LLM_MODEL and LLM_API_KEY must be set (and LLM_BASE_URL for non-OpenAI providers)")
        self.client = OpenAI(base_url=os.environ.get("LLM_BASE_URL") or None, api_key=os.environ["LLM_API_KEY"], timeout=timeout, max_retries=0)
        self.cache = cache
        self.sem = threading.Semaphore(concurrency)
        # Optional request parameters, dropped one at a time if the provider rejects them with a 400
        # (e.g. some providers refuse json_object mode, newer Claude models refuse temperature < 1).
        self._optional = {"response_format": {"type": "json_object"}, "temperature": 0, "max_tokens": 16000}
        self._lock = threading.Lock()

    def json(self, system: str, user: str, *, salt: str = "") -> dict:
        """Ask for a JSON object at temperature 0. ``salt`` forces a fresh, separately cached sample."""
        key = cache_key("llm", {"model": self.model, "system": system, "user": user, "salt": salt})
        if (hit := self.cache.get(key)) is not None:
            return hit
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

        def call():
            while True:
                with self._lock:
                    optional = dict(self._optional)
                try:
                    with self.sem:
                        return self.client.chat.completions.create(model=self.model, messages=messages, **optional)
                except Exception as e:
                    if getattr(e, "status_code", None) != 400 or not optional:
                        raise
                    named = [k for k in optional if k in str(e)]  # drop the parameter the error names, else the first
                    dropped = named[0] if named else next(iter(optional))
                    with self._lock:
                        self._optional.pop(dropped, None)
                    log.warning("LLM provider rejected an optional parameter; retrying without it", extra={"dropped": dropped, "error": str(e)[:200]})

        last_err = None
        for attempt in range(4):
            try:
                content = call().choices[0].message.content or ""
                result = parse_json_object(content)
                self.cache.put(key, result)
                return result
            except Exception as e:  # rate limits, timeouts and malformed JSON are all retried
                last_err = e
                time.sleep(2 ** attempt + random.uniform(0, 0.5))
        raise ModelError(f"LLM judge failed after retries: {last_err}")


def parse_json_object(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON object in LLM output: {text[:200]!r}")
    return json.loads(text[start:end + 1])
