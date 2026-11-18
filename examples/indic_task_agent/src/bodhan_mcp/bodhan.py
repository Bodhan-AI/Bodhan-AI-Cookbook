"""Client for the four Bodhan models.

Every paid call goes through ``BodhanClient``, which adds:

* retries with exponential backoff on 429, 5xx and network errors,
* a concurrency limit shared by all calls,
* a disk cache keyed by (model, endpoint, exact input), so the same input is never paid for twice,
* a rupee budget that stops the run before the next call would overspend,
* a ledger of every call (cached or not) for the trace.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.bodhan.ai"
RULES: dict[str, Any] = json.loads((Path(__file__).with_name("rules.json")).read_text(encoding="utf-8"))

# Prices in INR, from the hosted price list in the Cookbook rules.
PRICE = {
    "transcribe_per_audio_minute": 0.10,
    "translate_per_10k_output_tokens": 0.20,
    "speak_per_10k_input_chars": 6.0,
    "ocr_per_page": 0.20,
}

KEY_ENV = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}

RETRY_STATUS = {429, 500, 502, 503, 504}

# Second documented transport for indic-translate. Used only when POST /translate returns 404.
CHAT_ROUTE = "/v1/chat/completions"
CHAT_TRANSLATE_PROMPT = "Translate the following text into {language}.\n\n{text}"


def language_codes(model: str) -> list[str]:
    return list(RULES["models"][model]["languages"])


class BodhanError(Exception):
    """A structured error. ``code`` is stable, ``message`` says what to do next."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class BudgetExceeded(BodhanError):
    def __init__(self, spent: float, estimate: float, limit: float):
        super().__init__(
            "budget_exceeded",
            f"Stopped before the next call: it would cost about INR {estimate:.2f}, "
            f"and {spent:.2f} of the {limit:.2f} INR budget is already used. Raise --max-cost-inr to continue.",
        )


class Budget:
    def __init__(self, limit_inr: float = float("inf")):
        self.limit = limit_inr
        self.spent = 0.0
        self._lock = threading.Lock()

    def reserve(self, estimate: float) -> None:
        with self._lock:
            if self.spent + estimate > self.limit:
                raise BudgetExceeded(self.spent, estimate, self.limit)

    def charge(self, cost: float) -> None:
        with self._lock:
            self.spent += cost


class DiskCache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, key: str, value: dict) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def cache_key(**parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_error(resp: httpx.Response) -> BodhanError:
    code, message, request_id = "http_error", resp.text[:300] or f"HTTP {resp.status_code}", None
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        code = err.get("code", code)
        message = err.get("message", message)
        request_id = err.get("request_id")
    if request_id:
        message = f"{message} (request_id {request_id})"
    return BodhanError(code, message, retryable=resp.status_code in RETRY_STATUS, status=resp.status_code)


class BodhanClient:
    def __init__(
        self,
        *,
        cache_dir: Path,
        budget: Budget | None = None,
        max_concurrency: int = 4,
        max_attempts: int = 4,
        timeout_s: float = 90.0,
        env: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        env = dict(env) if env is not None else {}
        self.base_url = env.get("BODHAN_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        fallback = env.get("BODHAN_API_KEY")
        self.keys = {model: env.get(var) or fallback for model, var in KEY_ENV.items()}
        self.cache = DiskCache(cache_dir)
        self.budget = budget or Budget()
        self.max_attempts = max_attempts
        self._sem = threading.BoundedSemaphore(max_concurrency)
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, transport=transport)
        self._sleep = sleep
        self.translate_route = "/translate"
        self._ledger: list[dict[str, Any]] = []
        self._ledger_lock = threading.Lock()

    # ----- ledger -------------------------------------------------------
    @property
    def ledger(self) -> list[dict[str, Any]]:
        with self._ledger_lock:
            return list(self._ledger)

    def _record(self, **entry: Any) -> None:
        entry["at"] = time.time()
        with self._ledger_lock:
            self._ledger.append(entry)

    # ----- transport ----------------------------------------------------
    def _send(self, model: str, method: str, path: str, *, files=None, data=None, json_body=None, binary: bool = False):
        key = self.keys.get(model)
        if not key:
            raise BodhanError(
                "missing_key",
                f"No API key for {model}. Set {KEY_ENV[model]} or BODHAN_API_KEY in .env, then retry.",
            )
        headers = {"Authorization": f"Bearer {key}"}
        last: BodhanError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self._sem:
                    resp = self._http.request(method, path, headers=headers, files=files, data=data, json=json_body)
            except httpx.TransportError as exc:
                last = BodhanError("network_error", f"Could not reach Bodhan ({exc.__class__.__name__}).", retryable=True)
            else:
                if resp.status_code == 200:
                    if binary:
                        return resp.content
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise BodhanError("bad_response", "Bodhan returned a non-JSON body.") from exc
                last = _parse_error(resp)
                if not last.retryable:
                    raise last
            if attempt < self.max_attempts:
                self._sleep(2 ** (attempt - 1) + random.uniform(0, 0.5))
        assert last is not None
        raise last

    def _cached(self, *, model: str, endpoint: str, key_parts: dict, estimate: float, do_call, cost_of) -> dict:
        key = cache_key(model=model, endpoint=endpoint, **key_parts)
        hit = self.cache.get(key)
        if hit is not None:
            self._record(model=model, endpoint=endpoint, cached=True, cost_inr=0.0, latency_ms=0, status="cache_hit")
            return hit
        self.budget.reserve(estimate)
        started = time.monotonic()
        try:
            result = do_call()
        except BodhanError as exc:
            self._record(model=model, endpoint=endpoint, cached=False, cost_inr=0.0, latency_ms=_ms(started), status=exc.code)
            raise
        cost = cost_of(result)
        self.budget.charge(cost)
        self.cache.put(key, result)
        self._record(model=model, endpoint=endpoint, cached=False, cost_inr=round(cost, 4), latency_ms=_ms(started), status="ok")
        return result

    # ----- models -------------------------------------------------------
    def transcribe(self, wav_bytes: bytes, *, filename: str, language: str | None, duration_s: float) -> dict:
        """Speech to text for one WAV window of at most 30 seconds."""
        model, endpoint = "indic-transcribe", "/v1/audio/transcriptions"
        cost = duration_s / 60 * PRICE["transcribe_per_audio_minute"]

        def call() -> dict:
            data = {"model": model}
            if language:
                data["language"] = language
            body = self._send(model, "POST", endpoint, files={"file": (filename, wav_bytes, "audio/wav")}, data=data)
            if "text" not in body:
                raise BodhanError("bad_response", "Transcription response had no text field.")
            return {"text": body["text"]}

        return self._cached(
            model=model,
            endpoint=endpoint,
            key_parts={"audio": sha256_bytes(wav_bytes), "language": language},
            estimate=cost,
            do_call=call,
            cost_of=lambda _r: cost,
        )

    def translate(self, text: str, *, target_language: str, script: str = "native", max_output_tokens: int | None = None) -> dict:
        """Translation. Uses POST /translate; if the gateway answers 404 there, switches to the chat route for the rest of the run."""
        model, endpoint = "indic-translate", "/translate"
        body: dict[str, Any] = {"text": text, "target_language": target_language, "script": script}
        if max_output_tokens:
            body["max_output_tokens"] = max_output_tokens
        # Worst case is roughly one output token per input character.
        estimate = max(len(text), 20) / 10_000 * PRICE["translate_per_10k_output_tokens"]

        def call() -> dict:
            if self.translate_route == "/translate":
                try:
                    resp = self._send(model, "POST", endpoint, json_body=body)
                except BodhanError as exc:
                    if exc.status != 404:
                        raise
                    self.translate_route = CHAT_ROUTE  # /translate missing on this gateway; see README "Known API issues"
                else:
                    if "translation" not in resp:
                        raise BodhanError("bad_response", "Translation response had no translation field.")
                    usage = resp.get("usage") or {}
                    return {
                        "translation": resp["translation"],
                        "truncated": bool(resp.get("truncated", False)),
                        "completion_tokens": int(usage.get("completion_tokens", 0)),
                        "route": endpoint,
                    }
            return self._translate_via_chat(text, target_language, script)

        return self._cached(
            model=model,
            endpoint=endpoint,
            key_parts={"body": body},
            estimate=estimate,
            do_call=call,
            cost_of=lambda r: r["completion_tokens"] / 10_000 * PRICE["translate_per_10k_output_tokens"],
        )

    def _translate_via_chat(self, text: str, target_language: str, script: str) -> dict:
        if script != "native":
            raise BodhanError(
                "unsupported_on_fallback",
                f"script '{script}' needs POST /translate, which this gateway does not serve right now. Use script 'native'.",
            )
        language = RULES["language_names"].get(target_language, target_language)
        content = CHAT_TRANSLATE_PROMPT.format(language=language, text=text)
        resp = self._send(
            "indic-translate", "POST", CHAT_ROUTE, json_body={"model": "indic-translate", "messages": [{"role": "user", "content": content}]}
        )
        try:
            choice = resp["choices"][0]
            translation = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BodhanError("bad_response", "Translation (chat route) response did not have the expected shape.") from exc
        usage = resp.get("usage") or {}
        return {
            "translation": translation or "",
            "truncated": choice.get("finish_reason") == "length",
            "completion_tokens": int(usage.get("completion_tokens", 0)),
            "route": CHAT_ROUTE,
        }

    def speak(self, text: str, *, voice: str, lang: str, style: str | None = None) -> dict:
        """Text to speech. Returns base64 PCM16 WAV (24 kHz, mono)."""
        model, endpoint = "indic-speak", "/v1/audio/speech"
        instructions: dict[str, str] = {"lang": lang}
        if style:
            instructions["style"] = style
        body = {"model": model, "input": text, "voice": voice, "instructions": json.dumps(instructions, ensure_ascii=False)}
        cost = len(text) / 10_000 * PRICE["speak_per_10k_input_chars"]

        def call() -> dict:
            wav = self._send(model, "POST", endpoint, json_body=body, binary=True)
            return {"wav_b64": base64.b64encode(wav).decode("ascii")}

        return self._cached(
            model=model,
            endpoint=endpoint,
            key_parts={"body": body},
            estimate=cost,
            do_call=call,
            cost_of=lambda _r: cost,
        )

    def ocr(self, image_bytes: bytes, *, mime: str, table_format: str = "markdown") -> dict:
        """OCR of one whole PNG or JPEG page."""
        model, endpoint = "indic-ocr", "/v1/chat/completions"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        body = {
            "model": model,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
            "table_format": table_format,
        }
        cost = PRICE["ocr_per_page"]

        def call() -> dict:
            resp = self._send(model, "POST", endpoint, json_body=body)
            try:
                choice = resp["choices"][0]
                content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise BodhanError("bad_response", "OCR response did not have the expected shape.") from exc
            return {
                "text": content,
                "finish_reason": choice.get("finish_reason", "unknown"),
                "blocks": resp.get("blocks", []),
            }

        return self._cached(
            model=model,
            endpoint=endpoint,
            key_parts={"image": sha256_bytes(image_bytes), "mime": mime, "table_format": table_format},
            estimate=cost,
            do_call=call,
            cost_of=lambda _r: cost,
        )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
