"""Real Bodhan model calls with caching, cost ceilings, timeouts and redacted errors.

Requests implement the exact wire formats published in Bodhan-AI-Cookbook tutorials.
Do not replace these endpoints with third-party AI services.
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

import requests

from .config import key_for
from .errors import TaskError
from .store import Workspace

PRICES = {"transcribe_per_min": 0.10, "translate_per_10k_tokens": 0.20,
          "speak_per_10k_chars": 6.0, "ocr_per_image": 0.20}


class Budget:
    """Conservative preauthorization: no call is sent if its worst-case quote exceeds ceiling.

    Values are estimates, NOT the vendor's authoritative invoice. Failed calls can bill.
    """

    def __init__(self, maximum: float = 20.0):
        self.maximum = float(maximum)
        self.spent = 0.0
        self.events: list[dict[str, Any]] = []

    def reserve(self, quote: float, capability: str, cache_hit: bool = False) -> None:
        quote = round(max(0, quote), 5)
        if cache_hit:
            self.events.append({"capability": capability, "estimated_inr": 0.0, "cached": True})
            return
        if self.spent + quote > self.maximum + 1e-8:
            raise TaskError("BUDGET_EXCEEDED",
                            f"Estimated remaining budget ₹{self.maximum - self.spent:.2f}; "
                            f"next {capability} call reserves ₹{quote:.2f}")
        self.spent = round(self.spent + quote, 5)
        self.events.append({"capability": capability, "estimated_inr": quote, "cached": False})


class Gateway:
    def __init__(self, workspace: Workspace, budget: Budget | None = None,
                 session: requests.Session | None = None):
        self.workspace = workspace
        self.budget = budget or Budget()
        self.session = session or requests.Session()
        self.base_url = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")
        # Bodhan's product translation endpoint is /translate, not /v1/translate.
        # Accept the common mistaken base URL without silently breaking translation.
        if self.base_url == "https://api.bodhan.ai/v1":
            self.base_url = "https://api.bodhan.ai"
        self.timeout = min(240, max(5, float(os.environ.get("BODHAN_CALL_TIMEOUT", "120"))))
        self.calls: list[dict] = []

    def _post(self, capability: str, endpoint: str, *, payload: dict | None = None,
              file: tuple[str, bytes, str] | None = None, data: dict | None = None,
              is_audio: bool = False) -> dict | bytes:
        try:
            api_key = key_for(capability)
        except RuntimeError as exc:
            raise TaskError("CONFIG_MISSING", str(exc)) from exc
        headers = {"Authorization": f"Bearer {api_key}"}
        for attempt in range(3):
            kwargs: dict[str, Any] = {"headers": headers, "timeout": self.timeout}
            if file is not None:
                kwargs["files"] = {"file": file}
                kwargs["data"] = data or {}
            else:
                kwargs["json"] = payload
            start = time.monotonic()
            try:
                res = self.session.post(f"{self.base_url}{endpoint}", **kwargs)
            except requests.RequestException as exc:
                if attempt < 2:
                    time.sleep(0.4 * (2 ** attempt))
                    continue
                raise TaskError("UPSTREAM_UNREACHABLE", "Bodhan API unreachable", True) from exc
            latency = round((time.monotonic() - start) * 1000)
            if res.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                wait = res.headers.get("Retry-After", "")
                try:
                    # Respect the provider's suggested refill time when present.
                    # Keep the delay bounded for non-interactive task execution.
                    delay = min(30.0, max(0.5, float(wait))) if wait else (2.0 * (2 ** attempt))
                except ValueError:
                    delay = 2.0 * (2 ** attempt)
                time.sleep(delay + random.uniform(0, 0.05))
                continue
            if not res.ok:
                # Never reveal server response body verbatim (might echo credentials/PII).
                try:
                    err = res.json().get("error", {})
                    err_code = str(err.get("code", "unknown"))[:80]
                    msg = str(err.get("message", "API call unsuccessful"))[:300]
                except ValueError:
                    err_code, msg = "unknown", "API call unsuccessful"
                # A 404 from a correctly formed /translate route may be a gateway
                # routing problem rather than a client-side base-URL mistake.
                # The request id is safe to share with Bodhan support; never log keys.
                request_id = res.headers.get("x-request-id", "")
                ref = f" [request_id={request_id[:80]}]" if request_id else ""
                raise TaskError("BODHAN_API_ERROR", f"{capability}: HTTP {res.status_code} "
                                f"({err_code}) {msg}{ref}",
                                res.status_code in (429, 500, 502, 503, 504))
            self.calls.append({"capability": capability, "endpoint": endpoint,
                               "latency_ms": latency, "http_status": res.status_code})
            if is_audio:
                if not res.content:
                    raise TaskError("EMPTY_TTS", "Speech model returned an empty audio file")
                return res.content
            try:
                return res.json()
            except ValueError as exc:
                raise TaskError("BAD_MODEL_RESPONSE", f"{capability}: invalid JSON") from exc
        raise TaskError("UPSTREAM_UNREACHABLE", "Bodhan request did not complete", True)

    def translate(self, text: str, lang: str) -> tuple[str, bool]:
        if not text.strip():
            return "", True
        payload = {"text": text, "target_language": lang}
        key = self.workspace.cache_key("indic-translate", payload)
        cached = self.workspace.cached_json(key)
        # Reserve a conservative upper bound on output tokens; charged estimate stays conservative.
        quote = (max(20, len(text) * 3) / 10_000) * PRICES["translate_per_10k_tokens"]
        self.budget.reserve(quote, "translate", cache_hit=cached is not None)
        if cached is None:
            try:
                # Product REST endpoint, preferred in the Bodhan API reference.
                cached = self._post("translate", "/translate", payload=payload)
            except TaskError as exc:
                if exc.code != "BODHAN_API_ERROR" or "HTTP 404" not in str(exc):
                    raise
                # The official Bodhan console also documents Indic-Translate on
                # the OpenAI-compatible chat route. Only try it for a 404; never
                # paper over 401, 403, 429 or model errors with a second request.
                chat_payload = {
                    "model": "indic-translate",
                    "messages": [{"role": "user", "content": text}],
                    "target_language_code": lang,
                }
                try:
                    chat = self._post("translate", "/v1/chat/completions", payload=chat_payload)
                except TaskError as second:
                    raise TaskError(
                        "BODHAN_TRANSLATE_UNAVAILABLE",
                        "Both documented translation interfaces could not complete. "
                        f"REST: {exc}; chat: {second}. "
                        "If both return 404, send their request IDs to ishvinder@bodhan.ai.",
                        second.retryable,
                    ) from second
                if not isinstance(chat, dict):
                    raise TaskError("BAD_MODEL_RESPONSE", "Indic-Translate chat returned no JSON")
                try:
                    translated = chat["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as err:
                    raise TaskError("BAD_MODEL_RESPONSE", "Indic-Translate chat missing text content") from err
                cached = {"translation": translated}
            if not isinstance(cached, dict) or not isinstance(cached.get("translation"), str):
                raise TaskError("BAD_MODEL_RESPONSE", "Indic-Translate returned no translation field")
            self.workspace.save_json(key, cached)
        return cached["translation"], self.workspace.cached_json(key) is not None and self.budget.events[-1]["cached"]

    def transcribe(self, wav: Path, lang: str | None, seconds: float) -> tuple[dict, bool]:
        payload = {"sha256": self.workspace.fingerprint(wav), "language": lang}
        key = self.workspace.cache_key("indic-transcribe", payload)
        cached = self.workspace.cached_json(key)
        self.budget.reserve((seconds / 60) * PRICES["transcribe_per_min"],
                            "transcribe", cache_hit=cached is not None)
        if cached is None:
            data = {"model": "indic-transcribe"}
            if lang:
                data["language"] = lang
            cached = self._post("transcribe", "/v1/audio/transcriptions",
                                file=(wav.name, wav.read_bytes(), "audio/wav"), data=data)
            assert isinstance(cached, dict)
            if not isinstance(cached.get("text"), str):
                raise TaskError("BAD_MODEL_RESPONSE", "Indic-Transcribe returned no text")
            self.workspace.save_json(key, cached)
        return cached, self.budget.events[-1]["cached"]

    def speak(self, text: str, voice: str, lang: str, style: str = "") -> tuple[bytes, bool]:
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        payload = {"model": "indic-speak", "input": text, "voice": voice,
                   "instructions": json.dumps(instructions)}
        key = self.workspace.cache_key("indic-speak", payload)
        cached = self.workspace.cached_blob(key)
        self.budget.reserve(len(text) * PRICES["speak_per_10k_chars"] / 10_000,
                            "speak", cache_hit=cached is not None)
        if cached is None:
            result = self._post("speak", "/v1/audio/speech", payload=payload, is_audio=True)
            assert isinstance(result, bytes)
            cached = result
            self.workspace.save_blob(key, cached)
        return cached, self.budget.events[-1]["cached"]

    def ocr(self, path: Path) -> tuple[dict, bool]:
        import base64
        import mimetypes

        payload_key = {"sha256": self.workspace.fingerprint(path), "table_format": "markdown"}
        key = self.workspace.cache_key("indic-ocr", payload_key)
        cached = self.workspace.cached_json(key)
        self.budget.reserve(PRICES["ocr_per_image"], "ocr", cache_hit=cached is not None)
        if cached is None:
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            if mime not in ("image/jpeg", "image/png"):
                raise TaskError("BAD_IMAGE", "OCR supports PNG or JPEG page images")
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            body = {"model": "indic-ocr", "table_format": "markdown", "messages": [
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
                ]}
            ]}
            cached = self._post("ocr", "/v1/chat/completions", payload=body)
            assert isinstance(cached, dict)
            try:
                content = cached["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("empty")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise TaskError("BAD_MODEL_RESPONSE", "Indic-OCR returned no page content") from exc
            self.workspace.save_json(key, cached)
        return cached, self.budget.events[-1]["cached"]
