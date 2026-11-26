"""Model access. Every translation / TTS / ASR call goes to a Bodhan model; the LLM is only a judge.

`Services` wraps a backend with: cache (keyed by exact input), cost accounting + budget guard,
concurrency limit and retries with exponential backoff. Tests inject a fake backend.

API (from the Bodhan console docs; only BodhanBackend knows these details):
  translate  -> POST https://api.bodhan.ai/v1/chat/completions  {model: indic-translate, messages:[{user: text}],
                target_language}  (source auto-detected; verified working. The documented /translate URL returned 404.)
  speak      -> POST https://api.bodhan.ai/v1/audio/speech  {model: indic-speak, input, voice, instructions: '{"lang": "hi"}'} -> WAV
  transcribe -> POST https://api.bodhan.ai/v1/audio/transcriptions  multipart {file, model: indic-transcribe, language}
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
import time

from . import cost as costmod
from .audio import duration_seconds
from .cache import Cache
from .cost import BudgetExceeded, CostTracker

log = logging.getLogger("bodhan_eval")


class PermanentError(RuntimeError):
    """Do not retry (auth failure, bad request)."""


class NotConfigured(RuntimeError):
    pass


class BodhanBackend:
    def __init__(self, env=None):
        self.env = env if env is not None else os.environ
        self.base = self.env.get("BODHAN_BASE_URL", "https://api.bodhan.ai/v1")
        self.m_translate = "indic-translate"  # no model field is sent; used only in the cache key
        self.m_speak = self.env.get("BODHAN_SPEAK_MODEL", "indic-speak")
        self.m_transcribe = self.env.get("BODHAN_TRANSCRIBE_MODEL", "indic-transcribe")
        self.voice = self.env.get("BODHAN_SPEAK_VOICE", "Kavya")
        self.llm_model = self.env.get("LLM_MODEL", "")
        self._clients: dict[str, object] = {}

    def _key(self, model: str) -> str:
        """Per-model key (BODHAN_<MODEL>_KEY) first; the Cookbook-style BODHAN_API_KEY is an optional fallback."""
        return self.env.get(f"BODHAN_{model}_KEY") or self.env.get("BODHAN_API_KEY", "")

    def llm_configured(self) -> bool:
        return bool(self.env.get("LLM_BASE_URL") and self.env.get("LLM_API_KEY") and self.llm_model)

    def _client(self, name: str, base: str, key: str):
        if not key:
            raise NotConfigured(f"missing API key for {name}")
        if name not in self._clients:
            from openai import OpenAI

            self._clients[name] = OpenAI(base_url=base, api_key=key, max_retries=0, timeout=120)
        return self._clients[name]

    @staticmethod
    def _wrap(e: Exception):
        status = getattr(e, "status_code", None)
        if status is not None and 400 <= status < 500 and status not in (408, 429):
            return PermanentError(f"HTTP {status}: {e}")
        return e

    def voice_for(self, lang: str) -> str:
        """Per-language voice via BODHAN_SPEAK_VOICE_<LANG> (e.g. BODHAN_SPEAK_VOICE_TA), else the default."""
        return self.env.get(f"BODHAN_SPEAK_VOICE_{lang.upper()}", self.voice)

    def translate(self, text, src, tgt):
        # OpenAI-compatible route; `target_language` is a top-level body field. The source language is
        # auto-detected by the model, so `src` is only part of our cache key.
        c = self._client("translate", self.base, self._key("TRANSLATE"))
        try:
            r = c.chat.completions.create(model=self.m_translate,
                                          messages=[{"role": "user", "content": text}],
                                          extra_body={"target_language": tgt})
        except Exception as e:
            raise self._wrap(e) from e
        return r.choices[0].message.content or ""

    def speak(self, text, lang) -> bytes:
        c = self._client("speak", self.base, self._key("SPEAK"))
        try:
            r = c.audio.speech.create(model=self.m_speak, voice=self.voice_for(lang), input=text,
                                      extra_body={"instructions": json.dumps({"lang": lang})})
        except Exception as e:
            raise self._wrap(e) from e
        return r.read() if hasattr(r, "read") else r.content

    def transcribe(self, audio: bytes, lang) -> str:
        c = self._client("transcribe", self.base, self._key("TRANSCRIBE"))
        f = io.BytesIO(audio)
        f.name = "audio.wav"
        try:
            r = c.audio.transcriptions.create(model=self.m_transcribe, file=f, language=lang)
        except Exception as e:
            raise self._wrap(e) from e
        return getattr(r, "text", "") or ""

    def chat(self, messages, temperature):
        if not self.llm_configured():
            raise NotConfigured("LLM_BASE_URL / LLM_MODEL / LLM_API_KEY not set")
        c = self._client("llm", self.env["LLM_BASE_URL"], self.env["LLM_API_KEY"])
        try:
            r = c.chat.completions.create(model=self.llm_model, messages=messages, temperature=temperature)
        except Exception as e:
            raise self._wrap(e) from e
        u = getattr(r, "usage", None)
        usage = (getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0)) if u else (0, 0)
        return r.choices[0].message.content or "", usage


class Services:
    def __init__(self, backend, cache: Cache, cost: CostTracker, concurrency: int = 8,
                 retries: int = 4, backoff: float = 1.0, rl_retries: int = 8,
                 intervals: dict | None = None):
        self.backend, self.cache, self.cost = backend, cache, cost
        self._sem = threading.BoundedSemaphore(concurrency)
        self.retries, self.backoff, self.rl_retries = retries, backoff, rl_retries
        self.intervals = intervals or {}      # kind -> minimum seconds between call starts
        self._gate_lock = threading.Lock()
        self._next_ok: dict[str, float] = {}
        self._pause_until = 0.0               # shared: when a 429 hits, every thread waits

    def llm_available(self) -> bool:
        f = getattr(self.backend, "llm_configured", None)
        return bool(f()) if f else True

    @staticmethod
    def _is_rate_limit(e: Exception) -> bool:
        return getattr(e, "status_code", None) == 429 or type(e).__name__ == "RateLimitError" or "rate limit" in str(e).lower()

    def _wait_turn(self, kind: str) -> None:
        with self._gate_lock:
            now = time.monotonic()
            start = max(now, self._pause_until, self._next_ok.get(kind, 0.0))
            self._next_ok[kind] = start + self.intervals.get(kind, 0.0)
        if start > now:
            time.sleep(start - now)

    def _retry(self, fn, kind: str = "api"):
        attempt = 0
        while True:
            self._wait_turn(kind)
            try:
                with self._sem:
                    return fn()
            except (PermanentError, NotConfigured, BudgetExceeded):
                raise
            except Exception as e:
                rl = self._is_rate_limit(e)
                limit = self.rl_retries if rl else self.retries
                if attempt >= limit:
                    raise
                if rl:
                    delay = min(60.0, self.backoff * 5 * 2 ** attempt)
                    ra = getattr(getattr(e, "response", None), "headers", {}).get("retry-after") if hasattr(e, "response") else None
                    try:
                        delay = max(delay, min(120.0, float(ra)))
                    except (TypeError, ValueError):
                        pass
                    with self._gate_lock:  # tell every other thread to wait too
                        self._pause_until = max(self._pause_until, time.monotonic() + delay)
                else:
                    delay = self.backoff * 2 ** attempt
                log.warning("%s retry %d in %.0fs (%s): %s", kind, attempt + 1, delay, "rate limit" if rl else "error", str(e)[:120])
                time.sleep(delay)
                attempt += 1

    # ---- Indic-Translate ----
    def translate(self, text: str, src: str, tgt: str) -> str:
        payload = {"text": text, "src": src, "tgt": tgt, "model": getattr(self.backend, "m_translate", "t")}
        hit = self.cache.get("translate", payload)
        if hit:
            self.cost.record("translate", hit[1], True)
            return hit[0]
        self.cost.check(costmod.est_translate_cost(text))
        out = self._retry(lambda: self.backend.translate(text, src, tgt), "translate")
        c = costmod.est_translate_cost(out)
        self.cache.put("translate", payload, out, c)
        self.cost.record("translate", c, False)
        return out

    # ---- Indic-Speak ----
    def speak(self, text: str, lang: str) -> bytes:
        payload = {"text": text, "lang": lang, "model": getattr(self.backend, "m_speak", "s"),
                   "voice": (self.backend.voice_for(lang) if hasattr(self.backend, "voice_for") else getattr(self.backend, "voice", ""))}
        hit = self.cache.get("speak", payload)
        if hit and hit[2] is not None:
            self.cost.record("speak", hit[1], True)
            return hit[2]
        c = costmod.est_speak_cost(text)
        self.cost.check(c)
        audio = self._retry(lambda: self.backend.speak(text, lang), "speak")
        self.cache.put("speak", payload, {"bytes": len(audio)}, c, blob=audio)
        self.cost.record("speak", c, False)
        return audio

    # ---- Indic-Transcribe ----
    def transcribe(self, audio: bytes, lang: str) -> str:
        payload = {"audio_sha256": hashlib.sha256(audio).hexdigest(), "lang": lang,
                   "model": getattr(self.backend, "m_transcribe", "a")}
        hit = self.cache.get("transcribe", payload)
        if hit:
            self.cost.record("transcribe", hit[1], True)
            return hit[0]
        c = costmod.est_transcribe_cost(duration_seconds(audio))
        self.cost.check(c)
        out = self._retry(lambda: self.backend.transcribe(audio, lang), "transcribe")
        self.cache.put("transcribe", payload, out, c)
        self.cost.record("transcribe", c, False)
        return out

    # ---- LLM judge (not a Bodhan model; never used for translation / ASR / TTS) ----
    def chat(self, messages: list[dict], temperature: float, run: int) -> str:
        payload = {"messages": messages, "temperature": temperature, "run": run,
                   "model": getattr(self.backend, "llm_model", "llm")}
        hit = self.cache.get("llm", payload)
        if hit:
            self.cost.record("llm", 0.0, True)
            return hit[0]
        out, usage = self._retry(lambda: self.backend.chat(messages, temperature), "llm")
        self.cache.put("llm", payload, out, 0.0)
        self.cost.record("llm", 0.0, False)
        self.cost.add_tokens(*usage)
        return out
