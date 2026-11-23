"""Thin, defensive HTTP client for the four Bodhan models.

Responsibilities (and nothing else):
* auth per model, retries with exponential backoff + jitter on 429/5xx/timeouts,
* a process-wide concurrency limit,
* a content-addressed disk cache so the same input never goes to the same model twice,
* a cost/latency ledger the agent reads for budgets and reports.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import config
from .errors import MISSING_KEY, RATE_LIMITED, TIMEOUT, UPSTREAM, ToolError


@dataclass
class CallRecord:
    model: str
    cost_inr: float
    latency_ms: int
    cached: bool
    units: str


@dataclass
class Ledger:
    records: list[CallRecord] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, rec: CallRecord) -> None:
        with self.lock:
            self.records.append(rec)

    @property
    def total_cost(self) -> float:
        return round(sum(r.cost_inr for r in self.records if not r.cached), 4)

    def snapshot(self) -> int:
        return len(self.records)

    def since(self, mark: int) -> list[CallRecord]:
        return self.records[mark:]


class BodhanClient:
    def __init__(self, settings: config.Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.s = settings or config.Settings()
        self.ledger = Ledger()
        self._sem = threading.BoundedSemaphore(self.s.max_parallel)
        self._http = httpx.Client(base_url=self.s.base_url, timeout=self.s.request_timeout, transport=transport)
        self.s.cache_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._translate_via_chat = False

    # ------------------------------------------------------------------ cache
    def _cache_path(self, key: str, suffix: str) -> Path:
        return self.s.cache_dir / key[:2] / f"{key}{suffix}"

    @staticmethod
    def cache_key(model: str, payload: dict, blob: bytes | None = None) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
        if blob is not None:
            h.update(hashlib.sha256(blob).digest())
        return h.hexdigest()

    # ------------------------------------------------------------------ core
    def _request(self, model: str, key: str | None, method: str, url: str, **kw) -> httpx.Response:
        if not key:
            env = {"indic-transcribe": "BODHAN_TRANSCRIBE_KEY", "indic-translate": "BODHAN_TRANSLATE_KEY",
                   "indic-speak": "BODHAN_SPEAK_KEY", "indic-ocr": "BODHAN_OCR_KEY"}[model]
            raise ToolError(MISSING_KEY, f"No API key configured for {model}.", hint=f"Set {env} (or BODHAN_API_KEY).")
        headers = {"Authorization": f"Bearer {key}", **kw.pop("headers", {})}
        last: Exception | None = None
        for attempt in range(self.s.max_retries + 1):
            try:
                with self._sem:
                    resp = self._http.request(method, url, headers=headers, **kw)
            except httpx.TimeoutException as exc:
                last = ToolError(TIMEOUT, f"{model} timed out after {self.s.request_timeout:.0f}s.", retryable=True)
                last.__cause__ = exc
            except httpx.HTTPError as exc:
                last = ToolError(UPSTREAM, f"Network error calling {model}: {exc}", retryable=True)
            else:
                if resp.status_code < 400:
                    return resp
                err = _error_body(resp)
                if resp.status_code == 429:
                    last = ToolError(RATE_LIMITED, f"{model} rate limit or budget reached: {err}", retryable=True,
                                     hint="Wait and retry later; if this is the monthly budget, stop and report.")
                    retry_after = resp.headers.get("retry-after")
                    if attempt < self.s.max_retries:
                        # Honour Retry-After; otherwise back off much longer than for 5xx, since the
                        # hosted gateway's per-minute limits need seconds, not milliseconds, to refill.
                        wait = float(retry_after) if retry_after and retry_after.isdigit() else 5 * 2 ** attempt
                        time.sleep(min(wait, 40) * (0.75 + random.random() / 2))
                        continue
                elif resp.status_code >= 500:
                    last = ToolError(UPSTREAM, f"{model} server error {resp.status_code}: {err}", retryable=True)
                else:  # 4xx: our request is wrong; retrying will not help
                    raise ToolError(UPSTREAM, f"{model} rejected the request ({resp.status_code}): {err}",
                                    hint="Check the arguments; this error is not retryable.", status=resp.status_code)
            if attempt < self.s.max_retries:
                time.sleep(min(2 ** attempt, 16) * (0.5 + random.random() / 2))
        assert last is not None
        raise last

    def _cached_call(self, model: str, payload: dict, blob: bytes | None, binary: bool, fn, cost_fn, units: str):
        key = self.cache_key(model, payload, blob)
        path = self._cache_path(key, ".bin" if binary else ".json")
        with self._key_lock(key):  # identical concurrent requests wait for the first one: never paid twice
            if path.exists():
                data = path.read_bytes() if binary else json.loads(path.read_text(encoding="utf-8"))
                self.ledger.add(CallRecord(model, cost_fn(data), 0, True, units))
                return data
            t0 = time.monotonic()
            data = fn()
            ms = int((time.monotonic() - t0) * 1000)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
            if binary:
                tmp.write_bytes(data)
            else:
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)  # atomic: readers never see a half-written file
            self.ledger.add(CallRecord(model, cost_fn(data), ms, False, units))
            return data

    def _key_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    # ------------------------------------------------------------------ models
    def transcribe(self, wav: bytes, duration_sec: float, language: str | None) -> dict:
        payload = {"model": "indic-transcribe", **({"language": language} if language else {})}

        def call():
            r = self._request("indic-transcribe", self.s.transcribe_key, "POST", "/v1/audio/transcriptions",
                              files={"file": ("audio.wav", wav, "audio/wav")}, data=payload)
            return r.json()

        cost = round(duration_sec / 60 * config.PRICE_TRANSCRIBE_PER_MIN, 5)
        return self._cached_call("indic-transcribe", payload, wav, False, call, lambda _d: cost, f"{duration_sec:.1f}s")

    def translate(self, text: str, target: str, script: str = "native") -> dict:
        payload = {"text": text, "target_language": target, **({"script": script} if script != "native" else {})}

        def call():
            # Bodhan documents two equivalent transports. Prefer /translate; if the gateway does not
            # serve it (404, seen on 2026-11-23), use the OpenAI-style /v1/chat/completions transport.
            if not self._translate_via_chat:
                try:
                    return self._request("indic-translate", self.s.translate_key, "POST", "/translate", json=payload).json()
                except ToolError as e:
                    if e.details.get("status") != 404:
                        raise
                    self._translate_via_chat = True
            body = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], **payload}
            body.pop("text")
            d = self._request("indic-translate", self.s.translate_key, "POST", "/v1/chat/completions", json=body).json()
            ch = (d.get("choices") or [{}])[0]
            return {"translation": (ch.get("message") or {}).get("content") or "", "target_language": target,
                    "script": script, "truncated": ch.get("finish_reason") == "length", "usage": d.get("usage") or {}}

        def cost(d):
            toks = (d.get("usage") or {}).get("output_tokens") or (d.get("usage") or {}).get("completion_tokens")
            toks = toks if toks is not None else max(1, len(d.get("translation", "")) // 3)
            return round(toks / 10000 * config.PRICE_TRANSLATE_PER_10K_OUT_TOKENS, 5)

        return self._cached_call("indic-translate", payload, None, False, call, cost, f"{len(text)} chars in")

    def speak(self, text: str, voice: str, lang: str, style: str | None = None, attempt: int = 0) -> bytes:
        """`attempt` > 0 deliberately regenerates (used only after a failed back-transcription check)."""
        instructions = json.dumps({"lang": lang, **({"style": style} if style else {})})
        payload = {"model": "indic-speak", "input": text, "voice": voice, "instructions": instructions}
        key_payload = {**payload, **({"_attempt": attempt} if attempt else {})}

        def call():
            return self._request("indic-speak", self.s.speak_key, "POST", "/v1/audio/speech", json=payload).content

        cost = round(len(text) / 10000 * config.PRICE_SPEAK_PER_10K_CHARS, 5)
        return self._cached_call("indic-speak", key_payload, None, True, call, lambda _d: cost, f"{len(text)} chars")

    def ocr(self, image: bytes, mime: str, table_format: str = "markdown") -> dict:
        payload = {"model": "indic-ocr", "table_format": table_format}

        def call():
            body = {**payload, "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image).decode()}"}}]}]}
            return self._request("indic-ocr", self.s.ocr_key, "POST", "/v1/chat/completions", json=body).json()

        return self._cached_call("indic-ocr", payload, image, False, call, lambda _d: config.PRICE_OCR_PER_IMAGE, "1 page")


def _error_body(resp: httpx.Response) -> str:
    try:
        e = resp.json()["error"]
        return f"{e.get('code')}: {e.get('message')} (request_id={e.get('request_id')})"
    except Exception:  # noqa: BLE001 - any non-JSON body
        return resp.text[:300]
