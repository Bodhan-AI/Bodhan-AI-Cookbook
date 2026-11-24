"""HTTP client for the four Bodhan models.

Every paid call goes through `BodhanClient._call`, which gives us in one place:
  * a disk cache keyed by the exact request, so an identical input is never billed twice
  * a cost ledger with a hard cap, checked *before* the request is sent
  * a concurrency limit and retries with exponential backoff on 429 / 5xx / network errors
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from . import config
from .errors import BudgetExceeded, ToolError

log = logging.getLogger("bodhan.client")


class CostLedger:
    def __init__(self, cap_inr: float | None = None):
        self.cap = cap_inr
        self.spent = 0.0
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def check(self, estimate: float) -> None:
        with self._lock:
            if self.cap is not None and self.spent + estimate > self.cap + 1e-9:
                raise BudgetExceeded(self.spent, self.cap, estimate)

    def add(self, model: str, amount: float, cached: bool, detail: str = "") -> None:
        with self._lock:
            self.spent += amount
            self.calls.append({"model": model, "cost_inr": round(amount, 4), "cached": cached, "detail": detail})

    def by_model(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with self._lock:
            for c in self.calls:
                m = out.setdefault(c["model"], {"calls": 0, "cache_hits": 0, "cost_inr": 0.0})
                m["calls"] += 1
                m["cache_hits"] += int(c["cached"])
                m["cost_inr"] = round(m["cost_inr"] + c["cost_inr"], 4)
        return out


class DiskCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str, ext: str) -> Path:
        return self.root / f"{key}.{ext}"

    def get_json(self, key: str) -> Any | None:
        p = self._p(key, "json")
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except ValueError:
                return None
        return None

    def put_json(self, key: str, value: Any) -> None:
        tmp = self._p(key, "json.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
        tmp.replace(self._p(key, "json"))

    def get_bytes(self, key: str) -> bytes | None:
        p = self._p(key, "bin")
        return p.read_bytes() if p.exists() else None

    def put_bytes(self, key: str, value: bytes) -> None:
        tmp = self._p(key, "bin.tmp")
        tmp.write_bytes(value)
        tmp.replace(self._p(key, "bin"))


def _key(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, sort_keys=True, ensure_ascii=False).encode())
        h.update(b"\x00")
    return h.hexdigest()


RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class BodhanClient:
    def __init__(self, cache_dir: str | Path | None = None, cap_inr: float | None = None,
                 max_concurrency: int = 4, max_retries: int = 4, timeout_s: float = 90.0,
                 transport: httpx.BaseTransport | None = None, backoff_base: float = 1.0):
        cache_dir = cache_dir or os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")
        self.cache = DiskCache(Path(cache_dir))
        if cap_inr is None and os.environ.get("BODHAN_MAX_COST_INR"):
            cap_inr = float(os.environ["BODHAN_MAX_COST_INR"])
        self.ledger = CostLedger(cap_inr)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sem = threading.BoundedSemaphore(max_concurrency)
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    # -- core ---------------------------------------------------------------------------------------------------
    def _call(self, kind: str, model: str, key: str, estimate: float, send: Callable[[], httpx.Response],
              parse: Callable[[httpx.Response], Any], actual_cost: Callable[[Any], float], binary: bool = False,
              detail: str = "") -> tuple[Any, bool]:
        hit = self.cache.get_bytes(key) if binary else self.cache.get_json(key)
        if hit is not None:
            self.ledger.add(model, 0.0, True, detail)
            return hit, True

        self.ledger.check(estimate)
        last: ToolError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._sem:
                    resp = send()
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = ToolError("network_error", f"{model}: {type(e).__name__}: {e}",
                                 "Transient; the call is retried automatically.", retryable=True)
            else:
                if resp.status_code < 300:
                    value = parse(resp)
                    (self.cache.put_bytes if binary else self.cache.put_json)(key, value)
                    self.ledger.add(model, actual_cost(value), False, detail)
                    return value, False
                last = self._http_error(model, resp)
                if resp.status_code not in RETRY_STATUS or (resp.status_code == 502 and kind == "ocr"):
                    raise last
            if attempt < self.max_retries:
                delay = self.backoff_base * (2 ** attempt) + random.uniform(0, 0.25)
                log.warning("%s attempt %d failed (%s); retrying in %.1fs", model, attempt + 1, last.code, delay)
                time.sleep(delay)
        assert last is not None
        raise last

    @staticmethod
    def _http_error(model: str, resp: httpx.Response) -> ToolError:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else None) or body.get("detail") or resp.text[:200]
        if isinstance(msg, (list, dict)):
            msg = json.dumps(msg)[:300]
        s = resp.status_code
        if s == 401:
            return ToolError("auth_error", f"{model}: API key rejected. {msg}", "Check the BODHAN_*_KEY for this model.")
        if s == 413:
            return ToolError("payload_too_large", f"{model}: input too large. {msg}", "Send a smaller chunk.")
        if s == 429:
            return ToolError("rate_limited", f"{model}: rate limit hit. {msg}", "Retried with backoff.", retryable=True)
        if s in (400, 415, 422):
            return ToolError("bad_request", f"{model}: HTTP {s}: {msg}", "Fix the input; retrying will not help.")
        return ToolError("upstream_error", f"{model}: HTTP {s}: {msg}", "Upstream failure.", retryable=True)

    def _headers(self, kind: str) -> dict:
        return {"Authorization": f"Bearer {config.api_key(kind)}"}

    # -- models -------------------------------------------------------------------------------------------------
    def transcribe(self, wav: bytes, duration_s: float, language: str | None) -> dict:
        key = _key("stt", hashlib.sha256(wav).hexdigest(), language)

        def send() -> httpx.Response:
            data = {"model": "indic-transcribe"}
            if language:
                data["language"] = language
            return self._http.post(f"{config.API_BASE}/v1/audio/transcriptions", headers=self._headers("transcribe"),
                                   data=data, files={"file": ("chunk.wav", wav, "audio/wav")})

        est = duration_s / 60 * config.PRICE_TRANSCRIBE_PER_MIN
        val, cached = self._call("stt", "indic-transcribe", key, est, send, lambda r: {"text": r.json().get("text", "")},
                                 lambda _v: est, detail=f"{duration_s:.1f}s {language or 'auto'}")
        return {"text": val["text"].strip(), "cached": cached}

    def translate(self, text: str, target: str, script: str = "native", max_output_tokens: int | None = None) -> dict:
        """The documented POST /translate route returns 404 on the live gateway, so this uses the equivalent
        chat-completions transport: one user turn holding the text, plus a top-level `target_language`."""
        key = _key("mt", text, target, script, max_output_tokens)
        body: dict[str, Any] = {"model": "indic-translate", "messages": [{"role": "user", "content": text}],
                                "target_language": target}
        if script != "native":
            body["script"] = script
        if max_output_tokens:
            body["max_output_tokens"] = max_output_tokens

        def send() -> httpx.Response:
            return self._http.post(f"{config.API_BASE}/v1/chat/completions", headers=self._headers("translate"), json=body)

        def parse(r: httpx.Response) -> dict:
            j = r.json()
            choice = j["choices"][0]
            return {"translation": choice["message"]["content"] or "", "truncated": choice.get("finish_reason") == "length",
                    "tokens": (j.get("usage") or {}).get("completion_tokens", 0)}

        est = max(len(text) / 3, 20) / 10000 * config.PRICE_TRANSLATE_PER_10K_TOKENS
        val, cached = self._call("mt", "indic-translate", key, est, send, parse,
                                 lambda v: v["tokens"] / 10000 * config.PRICE_TRANSLATE_PER_10K_TOKENS,
                                 detail=f"->{target} {len(text)} chars")
        return {**val, "cached": cached}

    def speak(self, text: str, voice: str, lang: str, style: str | None = None) -> dict:
        instr = {"lang": lang}
        if style:
            instr["style"] = style
        key = _key("tts", text, voice, instr)
        est = len(text) / 10000 * config.PRICE_SPEAK_PER_10K_CHARS

        def send() -> httpx.Response:
            return self._http.post(f"{config.API_BASE}/v1/audio/speech", headers=self._headers("speak"),
                                   json={"model": "indic-speak", "input": text, "voice": voice,
                                         "instructions": json.dumps(instr)})

        val, cached = self._call("tts", "indic-speak", key, est, send, lambda r: r.content, lambda _v: est,
                                 binary=True, detail=f"{voice}/{lang} {len(text)} chars")
        return {"wav": val, "cached": cached}

    def ocr(self, image: bytes, mime: str = "image/png", table_format: str = "markdown", max_tokens: int = 2048) -> dict:
        key = _key("ocr", hashlib.sha256(image).hexdigest(), table_format, max_tokens)
        b64 = base64.b64encode(image).decode()

        def send() -> httpx.Response:
            return self._http.post(f"{config.API_BASE}/v1/chat/completions", headers=self._headers("ocr"), json={
                "model": "indic-ocr", "table_format": table_format, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]})

        def parse(r: httpx.Response) -> dict:
            j = r.json()
            return {"markdown": j["choices"][0]["message"]["content"], "blocks": j.get("blocks", [])}

        val, cached = self._call("ocr", "indic-ocr", key, config.PRICE_OCR_PER_IMAGE, send, parse,
                                 lambda _v: config.PRICE_OCR_PER_IMAGE, detail=f"{len(image) // 1024} KB")
        return {**val, "cached": cached}


_client: BodhanClient | None = None
_client_lock = threading.Lock()


def get_client() -> BodhanClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = BodhanClient()
        return _client


def set_client(c: BodhanClient | None) -> None:
    global _client
    with _client_lock:
        _client = c
