"""The one shared Bodhan HTTP client.

* per-model API keys           * retries with exponential backoff (+ Retry-After)
* global concurrency semaphore * per-call timeout
* content-addressed disk cache * cost + latency on every call
"""
from __future__ import annotations

import base64
import json
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import config, pricing
from .cache import cache, make_key, sha256_bytes
from .errors import ToolFailure

RETRY_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


@dataclass
class CallInfo:
    """What one logical Bodhan call cost. Cached calls cost nothing and say so."""

    model: str
    cost_inr: float = 0.0
    latency_s: float = 0.0
    cached: bool = False
    attempts: int = 1

    def as_dict(self) -> dict:
        return {"model": self.model, "cost_inr": round(self.cost_inr, 5), "latency_s": round(self.latency_s, 3), "cached": self.cached}


@dataclass
class Ledger:
    """Process-wide accounting (the agent keeps its own per-run budget from tool results)."""

    calls: list[CallInfo] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, info: CallInfo) -> None:
        with self._lock:
            self.calls.append(info)

    @property
    def total_cost(self) -> float:
        return sum(c.cost_inr for c in self.calls)


ledger = Ledger()


def _error_from_response(model: str, resp: httpx.Response) -> ToolFailure:
    message, code = "", ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            message, code = str(err.get("message", "")), str(err.get("code") or err.get("type") or "")
        elif isinstance(body, dict):
            message = str(body.get("detail", ""))
    except ValueError:
        message = resp.text[:200]
    s = resp.status_code
    if s in (401, 403):
        return ToolFailure("AUTH_FAILED", f"{model} rejected the API key ({s} {code}).",
                           hint="Check the key for this model in .env; keys are scoped per model (a key for one model is refused by another).")
    if s == 429:
        return ToolFailure("RATE_LIMITED", f"{model} rate limit or budget reached (429). {message}".strip(), retryable=True,
                           hint="Wait and retry later, or reduce parallelism (BODHAN_MAX_CONCURRENCY). Credit may be exhausted.")
    if s == 413:
        return ToolFailure("INPUT_TOO_LARGE", f"{model} refused the input as too large (413).", hint="Use a smaller input.")
    if s in (400, 422):
        return ToolFailure("UPSTREAM_REJECTED_INPUT", f"{model} rejected the request ({s}): {message}", hint="Check language code, voice and input size.")
    return ToolFailure("UPSTREAM_ERROR", f"{model} returned HTTP {s}: {message}", retryable=s >= 500,
                       hint="Retry later; if it persists the Bodhan API may be down.")


class BodhanClient:
    def __init__(self) -> None:
        self._sem: threading.BoundedSemaphore | None = None
        self._http: httpx.Client | None = None
        self._transport: httpx.BaseTransport | None = None  # injectable for tests
        self.sleep = time.sleep  # injectable for tests

    # -- plumbing ---------------------------------------------------------------------------------
    def configure_for_tests(self, transport: httpx.BaseTransport | None) -> None:
        self._transport = transport
        self._http = None

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(base_url=config.BASE_URL, timeout=config.call_timeout_s(), transport=self._transport)
        return self._http

    @property
    def sem(self) -> threading.BoundedSemaphore:
        if self._sem is None:
            self._sem = threading.BoundedSemaphore(config.max_concurrency())
        return self._sem

    def _post(self, model: str, path: str, *, json_body: dict | None = None, data: dict | None = None,
              files: dict | None = None, timeout: float | None = None, retry_timeouts: bool = True) -> httpx.Response:
        key = config.get_key(model)
        last: ToolFailure | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with self.sem:
                    resp = self.http.post(path, headers={"Authorization": f"Bearer {key}"}, json=json_body, data=data, files=files,
                                          timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
            except httpx.TimeoutException:
                last = ToolFailure("UPSTREAM_TIMEOUT", f"{model} did not answer within {(timeout or config.call_timeout_s()):.0f}s.", retryable=True,
                                   hint="Retry; if it repeats, try a smaller input.")
                if not retry_timeouts:
                    raise last
            except httpx.TransportError as exc:
                last = ToolFailure("NETWORK_ERROR", f"Could not reach the Bodhan API: {type(exc).__name__}.", retryable=True,
                                   hint="Check connectivity and retry.")
            else:
                if resp.status_code < 400:
                    return resp
                last = _error_from_response(model, resp)
                if resp.status_code not in RETRY_STATUS:
                    raise last
                # 429 is a budget/rate signal: back off but do not hammer.
                if resp.status_code == 429 and attempt >= 2:
                    raise last
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    self.sleep(min(int(retry_after), 30))
                    continue
            if attempt < MAX_ATTEMPTS:
                self.sleep(min(2 ** (attempt - 1), 8) + random.uniform(0, 0.25))
        assert last is not None
        raise last

    # -- models -----------------------------------------------------------------------------------
    def transcribe(self, wav_path: Path, duration_s: float, language: str | None) -> tuple[str, CallInfo]:
        model = "indic-transcribe"
        audio = wav_path.read_bytes()
        ckey = make_key(model, sha256_bytes(audio), language or "")
        t0 = time.perf_counter()
        hit = cache.get_json(ckey)
        if hit is not None:
            info = CallInfo(model, 0.0, time.perf_counter() - t0, cached=True)
            ledger.add(info)
            return hit["text"], info
        data = {"model": model}
        if language:
            data["language"] = language
        resp = self._post(model, "/v1/audio/transcriptions", data=data, files={"file": (wav_path.name, audio, "audio/wav")})
        text = str(resp.json().get("text") or "").strip()
        cache.put_json(ckey, {"text": text})
        info = CallInfo(model, pricing.transcribe_cost(duration_s), time.perf_counter() - t0)
        ledger.add(info)
        return text, info

    def translate(self, text: str, target_language: str, script: str = "native") -> tuple[str, bool, CallInfo]:
        """Returns (translation, truncated, info). Source language is auto-detected by the model."""
        model = "indic-translate"
        ckey = make_key(model, text, target_language, script)
        t0 = time.perf_counter()
        hit = cache.get_json(ckey)
        if hit is not None:
            info = CallInfo(model, 0.0, time.perf_counter() - t0, cached=True)
            ledger.add(info)
            return hit["translation"], bool(hit.get("truncated")), info
        body: dict = {"model": model, "messages": [{"role": "user", "content": text}], "target_language_code": target_language}
        if target_language != "en":
            body["target_script"] = script
        # NOTE: the documented POST /translate route returns 404 on the hosted API today;
        # the documented chat-completions equivalent is used (same model, same prices).
        resp = self._post(model, "/v1/chat/completions", json_body=body)
        j = resp.json()
        choice = (j.get("choices") or [{}])[0]
        translation = str((choice.get("message") or {}).get("content") or "").strip()
        truncated = choice.get("finish_reason") == "length"
        tokens = int((j.get("usage") or {}).get("completion_tokens") or pricing.estimate_translate_tokens(translation))
        cache.put_json(ckey, {"translation": translation, "truncated": truncated})
        info = CallInfo(model, pricing.translate_cost(tokens), time.perf_counter() - t0)
        ledger.add(info)
        return translation, truncated, info

    def speak(self, text: str, language: str, voice: str, style: str | None = None, force_refresh: bool = False,
              timeout_s: float = 60.0) -> tuple[Path, CallInfo]:
        """Returns (wav path inside the cache, info). One short input per call."""
        model = "indic-speak"
        instructions = {"lang": language, **({"style": style} if style else {})}
        ckey = make_key(model, text, language, voice, style or "")
        t0 = time.perf_counter()
        if not force_refresh:
            hit = cache.get_file(ckey, ".wav")
            if hit is not None:
                info = CallInfo(model, 0.0, time.perf_counter() - t0, cached=True)
                ledger.add(info)
                return hit, info
        # A request that has not finished in `timeout_s` is usually a runaway generation: do not repeat it unchanged
        # (the tool splits the text and retries the halves).
        resp = self._post(model, "/v1/audio/speech", timeout=timeout_s, retry_timeouts=False,
                          json_body={"model": model, "input": text, "voice": voice, "instructions": json.dumps(instructions)})
        if not resp.content[:4] == b"RIFF":
            raise ToolFailure("UPSTREAM_ERROR", "indic-speak returned something that is not WAV audio.", retryable=True, hint="Retry.")
        path = cache.put_bytes(ckey, ".wav", resp.content)
        info = CallInfo(model, pricing.speak_cost(len(text)), time.perf_counter() - t0)
        ledger.add(info)
        return path, info

    def ocr_page(self, image_bytes: bytes, mime: str, table_format: str = "markdown") -> tuple[dict, CallInfo]:
        model = "indic-ocr"
        ckey = make_key(model, sha256_bytes(image_bytes), table_format)
        t0 = time.perf_counter()
        hit = cache.get_json(ckey)
        if hit is not None:
            info = CallInfo(model, 0.0, time.perf_counter() - t0, cached=True)
            ledger.add(info)
            return hit, info
        url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
        body = {"model": model, "table_format": table_format,
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
        resp = self._post(model, "/v1/chat/completions", json_body=body)
        j = resp.json()
        choice = (j.get("choices") or [{}])[0]
        blocks = j.get("blocks") or []
        confs = [float(b["conf"]) for b in blocks if isinstance(b.get("conf"), (int, float))]
        result = {
            "markdown": str((choice.get("message") or {}).get("content") or "").strip(),
            "truncated": choice.get("finish_reason") == "length",
            "mean_conf": (sum(confs) / len(confs)) if confs else None,
            "n_blocks": len(blocks),
        }
        cache.put_json(ckey, result)
        info = CallInfo(model, pricing.ocr_cost(1), time.perf_counter() - t0)
        ledger.add(info)
        return result, info


client = BodhanClient()
