"""HTTP client for the four Bodhan models.

Every call goes through `_call`, which adds four things in one place:
  1. a disk cache keyed by the exact input, so the same input is never paid for twice
  2. a budget check before any paid request
  3. a concurrency limit
  4. retries with exponential backoff on rate limits, timeouts and server errors
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import config
from .errors import ToolError

log = logging.getLogger("bodhan_mcp.client")


@dataclass
class Ledger:
    """Thread-safe record of what each model call cost."""

    max_cost_inr: float | None = None
    spent_inr: float = 0.0
    calls: int = 0
    cache_hits: int = 0
    by_model: dict[str, dict] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self, model: str, estimate_inr: float) -> None:
        """Refuse a call that would take spending past the budget."""
        with self._lock:
            if self.max_cost_inr is not None and self.spent_inr + estimate_inr > self.max_cost_inr + 1e-9:
                left = max(0.0, self.max_cost_inr - self.spent_inr)
                raise ToolError(
                    "budget_exceeded",
                    f"This {model} call needs about ₹{estimate_inr:.3f} but only ₹{left:.3f} of the ₹{self.max_cost_inr:.2f} budget is left.",
                    hint="Stop making paid calls. Finish now and report what was completed.",
                )

    def record(self, model: str, cost_inr: float, cached: bool) -> None:
        with self._lock:
            row = self.by_model.setdefault(model, {"calls": 0, "cache_hits": 0, "cost_inr": 0.0})
            if cached:
                self.cache_hits += 1
                row["cache_hits"] += 1
            else:
                self.calls += 1
                self.spent_inr += cost_inr
                row["calls"] += 1
                row["cost_inr"] = round(row["cost_inr"] + cost_inr, 6)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "spent_inr": round(self.spent_inr, 4),
                "max_cost_inr": self.max_cost_inr,
                "paid_calls": self.calls,
                "cache_hits": self.cache_hits,
                "by_model": json.loads(json.dumps(self.by_model)),
            }


class DiskCache:
    """One file per model output, named by the SHA-256 of its exact input."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / key

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def put(self, key: str, value: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp{threading.get_ident()}")
        tmp.write_bytes(value)
        for attempt in range(5):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                # Windows refuses to replace a file another thread is writing or reading at that moment.
                # The key is a hash of the input, so a file already there holds the same output: keep it.
                if path.exists():
                    tmp.unlink(missing_ok=True)
                    return
                time.sleep(0.05 * (attempt + 1))
        tmp.unlink(missing_ok=True)  # caching is an optimisation; failing to cache must not fail the call


def _key(*parts: object) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else json.dumps(part, sort_keys=True, ensure_ascii=False).encode())
        h.update(b"\x00")
    return h.hexdigest()


_GAVE_UP = ("did not return a usable", "was not transcribed", "could not be transcribed", "no usable")


def _model_gave_up(text: str) -> bool:
    """A 5xx whose message says the model processed the input and failed on it (not an outage)."""
    low = text.lower()
    return any(phrase in low for phrase in _GAVE_UP)


class BodhanClient:
    def __init__(self, settings: config.Settings, ledger: Ledger | None = None, session=None):
        self.settings = settings
        self.ledger = ledger or Ledger(max_cost_inr=settings.max_cost_inr)
        self.cache = DiskCache(settings.cache_dir)
        self.session = session or requests.Session()
        self._slots = threading.Semaphore(settings.max_parallel)

    # ---- shared plumbing -------------------------------------------------

    def _headers(self, model: str) -> dict:
        key = self.settings.keys.get(model, "")
        if not key:
            raise ToolError(
                "missing_api_key",
                f"No API key is set for the {model} model.",
                hint=f"Set {config.KEY_VARS[model]} (or BODHAN_API_KEY) in the environment.",
            )
        return {"Authorization": f"Bearer {key}"}

    def _post(self, model: str, path: str, **kwargs) -> requests.Response:
        """POST with retries. Raises ToolError with a clear code when it gives up."""
        url = f"{self.settings.base_url}{path}"
        headers = {**self._headers(model), **kwargs.pop("headers", {})}
        last = "no response"
        for attempt in range(self.settings.max_retries + 1):
            try:
                with self._slots:
                    resp = self.session.post(url, headers=headers, timeout=self.settings.timeout_s, **kwargs)
            except requests.Timeout:
                last, code = f"timed out after {self.settings.timeout_s:.0f} s", "timeout"
            except requests.RequestException as exc:
                last, code = f"network error: {exc.__class__.__name__}", "upstream_error"
            else:
                if resp.status_code < 400:
                    return resp
                last, code = _error_text(resp), "rate_limited" if resp.status_code == 429 else "upstream_error"
                if resp.status_code not in (408, 429) and resp.status_code < 500:
                    # 4xx other than rate limits will not improve on retry.
                    raise ToolError("upstream_error", f"{model} rejected the request: {last}", hint="Check the arguments; retrying the same call will not help.")
                if _model_gave_up(last):
                    # The model ran and said it could not handle this input; the same request will fail again.
                    raise ToolError("model_could_not_process", f"{model} could not process this input: {last}", hint="Retrying the same input will not help. Report this part as not processed, or try a smaller or clearer input.")
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    time.sleep(min(float(retry_after), 30.0))
                    continue
            if attempt < self.settings.max_retries:
                delay = min(20.0, 0.8 * 2**attempt) * (0.7 + 0.6 * random.random())
                log.warning("retry model=%s attempt=%d reason=%s wait=%.1fs", model, attempt + 1, last, delay)
                time.sleep(delay)
        raise ToolError(code, f"{model} failed after {self.settings.max_retries + 1} attempts: {last}", hint="Try again later or continue without this step.", retryable=True)

    def _call(self, model: str, cache_key: str, estimate_inr: float, do, cost_of=None) -> bytes:
        """Cache lookup, budget check, request, then record the real cost."""
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.ledger.record(model, 0.0, cached=True)
            return cached
        self.ledger.reserve(model, estimate_inr)
        started = time.monotonic()
        raw = do()
        cost = cost_of(raw) if cost_of else estimate_inr
        self.ledger.record(model, cost, cached=False)
        self.cache.put(cache_key, raw)
        log.info("call model=%s cost_inr=%.4f latency_ms=%d", model, cost, (time.monotonic() - started) * 1000)
        return raw

    # ---- the four models -------------------------------------------------

    def transcribe(self, wav: bytes, duration_s: float, language: str | None = None) -> dict:
        """Speech to text for one clip of up to ~30 s. Returns {"text": ...}."""
        data = {"model": "indic-transcribe"}
        if language:
            data["language"] = language

        def do() -> bytes:
            resp = self._post("transcribe", "/v1/audio/transcriptions", files={"file": ("clip.wav", wav, "audio/wav")}, data=data)
            return json.dumps(resp.json(), ensure_ascii=False).encode()

        cost = duration_s / 60.0 * config.PRICE_TRANSCRIBE_PER_MIN
        return json.loads(self._call("transcribe", _key("transcribe", wav, language), cost, do))

    def translate(self, text: str, target_language: str, script: str = "native") -> dict:
        """Translate one chunk of text. Returns {"translation", "truncated", "usage"}.

        The live API serves indic-translate on the OpenAI-style chat route
        (POST /v1/chat/completions, model "indic-translate", with `target_language`
        beside the messages). The Cookbook documents POST /translate, which returned
        404 when tested on 17 Nov 2026, so the chat route is used.
        """
        body = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], "target_language": target_language}
        if script != "native":
            body["script"] = script

        def do() -> bytes:
            out = self._post("translate", "/v1/chat/completions", json=body, headers={"Content-Type": "application/json"}).json()
            try:
                choice = out["choices"][0]
                translation = choice["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                raise ToolError("upstream_error", "The translation model returned a response in an unexpected shape.", retryable=True) from None
            result = {"translation": translation.strip(), "truncated": choice.get("finish_reason") == "length", "usage": out.get("usage") or {}}
            return json.dumps(result, ensure_ascii=False).encode()

        def cost_of(raw: bytes) -> float:
            out = json.loads(raw)
            usage = out.get("usage") or {}
            tokens = usage.get("completion_tokens") or usage.get("output_tokens") or len(out.get("translation", "")) / 2
            return tokens / 10_000 * config.PRICE_TRANSLATE_PER_10K_TOKENS

        estimate = len(text) / 10_000 * config.PRICE_TRANSLATE_PER_10K_TOKENS  # about one token per input character, an upper bound
        return json.loads(self._call("translate", _key("translate-chat", body), estimate, do, cost_of))

    def speak(self, text: str, voice: str, language: str, style: str | None = None, attempt: int = 0) -> bytes:
        """Text to speech for a sentence or two. Returns WAV bytes (PCM16, 24 kHz, mono).

        `attempt` is part of the cache key. A deliberate retry after a failed
        check must reach the model again instead of replaying the bad audio.
        """
        instructions = {"lang": language}
        if style:
            instructions["style"] = style
        body = {"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instructions, ensure_ascii=False)}

        def do() -> bytes:
            return self._post("speak", "/v1/audio/speech", json=body, headers={"Content-Type": "application/json"}).content

        cost = len(text) / 10_000 * config.PRICE_SPEAK_PER_10K_CHARS
        return self._call("speak", _key("speak", body, attempt), cost, do)

    def ocr(self, image: bytes, mime: str = "image/png", table_format: str = "markdown") -> dict:
        """OCR one page image. Returns {"markdown": ..., "blocks": [...]}."""
        url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        body = {"model": "indic-ocr", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}], "table_format": table_format}

        def do() -> bytes:
            out = self._post("ocr", "/v1/chat/completions", json=body, headers={"Content-Type": "application/json"}).json()
            page = {"markdown": out["choices"][0]["message"]["content"] or "", "blocks": out.get("blocks", [])}
            return json.dumps(page, ensure_ascii=False).encode()

        return json.loads(self._call("ocr", _key("ocr", image, table_format), config.PRICE_OCR_PER_IMAGE, do))


def _error_text(resp) -> str:
    """Bodhan errors are JSON: {"error": {"message", "code", "request_id"}}."""
    try:
        err = resp.json()["error"]
        return f"HTTP {resp.status_code} {err.get('code')}: {err.get('message')} (request_id={err.get('request_id')})"
    except Exception:  # noqa: BLE001 - any parse problem falls back to the status line
        return f"HTTP {resp.status_code}"
