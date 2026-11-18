"""Cached HTTP calls, finite retries and shared cost reservations."""

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import httpx


class Unavailable(RuntimeError):
    pass


class BudgetExceeded(Unavailable):
    pass


class Ledger:
    def __init__(self, maximum):
        self.maximum = maximum
        self.used = 0.0
        self.reserved = 0.0
        self.lock = threading.RLock()

    def reserve(self, amount):
        with self.lock:
            if self.used + self.reserved + amount > self.maximum + 1e-9:
                raise BudgetExceeded("Cost budget exhausted before the next request")
            self.reserved += amount

    def settle(self, reservation, charge):
        with self.lock:
            self.reserved -= reservation
            self.used += charge


class ModelClient:
    def __init__(self, cache, ledger, *, offline=False, concurrency=2, timeout=60,
                 llm_input_price=None, llm_output_price=None, transport=None):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.offline = offline
        self.limit = threading.BoundedSemaphore(concurrency)
        self.locks_guard = threading.Lock()
        self.locks = {}
        self.http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)
        self.llm_input_price = llm_input_price
        self.llm_output_price = llm_output_price
        self.events = []
        self.events_lock = threading.Lock()
        self.context = threading.local()

    def event(self, value):
        if getattr(self.context, "item_id", None) is not None:
            value = {"item_id": self.context.item_id, **value}
        with self.events_lock:
            self.events.append(value)

    def request(self, operation, identity, url, key, reservation, *, payload=None,
                audio_path=None, language=None):
        digest = hashlib.sha256(json.dumps(
            {"version": 1, "operation": operation, "endpoint": url, "input": identity},
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        target = self.cache / f"{digest}.json"
        with self.locks_guard:
            lock = self.locks.setdefault(digest, threading.Lock())
        started = time.monotonic()
        with lock:
            if target.exists():
                value = json.loads(target.read_text())
                self.event({"operation": operation, "cache_key": digest, "cache_hit": True,
                            "charged_inr": 0, "original_cost_inr": value["cost_inr"]})
                return value["result"]
            if self.offline:
                raise Unavailable(f"No cached {operation} response in offline mode")
            if not key or key.startswith("YOUR_"):
                raise Unavailable(f"Missing credentials for {operation}")
            for attempt in range(3):
                self.ledger.reserve(reservation)
                try:
                    with self.limit:
                        if audio_path:
                            with open(audio_path, "rb") as audio:
                                response = self.http.post(
                                    url, headers={"Authorization": f"Bearer {key}"},
                                    files={"file": ("audio.wav", audio, "audio/wav")},
                                    data={"model": "indic-transcribe", "language": language})
                        else:
                            response = self.http.post(url, headers={"Authorization": f"Bearer {key}"},
                                                      json=payload)
                    if response.status_code in (408, 429) or response.status_code >= 500:
                        # A returned HTTP error has no successful model output. Reserve
                        # the full amount anyway because upstream billing is unknown.
                        self.ledger.settle(reservation, reservation)
                        retry_after = response.headers.get("retry-after", "")
                        try:
                            delay = min(10, max(1, float(retry_after)))
                        except ValueError:
                            delay = 2 ** attempt
                        self.event({"operation": operation, "cache_key": digest,
                                    "attempt": attempt + 1, "status": response.status_code,
                                    "charged_inr": reservation, "billing": "conservative_reservation"})
                        if attempt == 2:
                            raise Unavailable(f"{operation} failed after bounded retries (HTTP {response.status_code})")
                        time.sleep(delay)
                        continue
                    response.raise_for_status()
                    if operation == "speak":
                        if not response.content.startswith(b"RIFF") or len(response.content) < 44:
                            raise ValueError("Speech endpoint did not return WAV audio")
                        blob = self.cache / f"{digest}.wav"
                        blob.write_bytes(response.content)
                        result = {"audio_path": str(blob.resolve())}
                        cost = reservation
                    else:
                        result = response.json()
                        if "error" in result:
                            raise ValueError("Model returned an error envelope")
                        cost = self.actual_cost(operation, result, reservation)
                    self.ledger.settle(reservation, cost)
                except (httpx.TimeoutException, httpx.TransportError):
                    # A timeout may have been billed. Do not submit a duplicate.
                    self.ledger.settle(reservation, reservation)
                    self.event({"operation": operation, "cache_key": digest,
                                "charged_inr": reservation, "error": "network_timeout_billing_unknown"})
                    raise Unavailable(f"{operation} network failure; charged its reservation; no duplicate retry") from None
                except (httpx.HTTPStatusError, ValueError, OSError):
                    self.ledger.settle(reservation, reservation)
                    raise Unavailable(f"{operation} request or response failed; reservation retained") from None
                else:
                    record = {"result": result, "cost_inr": cost}
                    temporary = target.with_suffix(".tmp")
                    temporary.write_text(json.dumps(record, ensure_ascii=False))
                    temporary.replace(target)
                    self.event({"operation": operation, "cache_key": digest, "cache_hit": False,
                                "charged_inr": cost, "latency_seconds": round(time.monotonic() - started, 4)})
                    return result
        raise Unavailable(f"{operation} did not finish")

    def actual_cost(self, operation, result, reservation):
        usage = result.get("usage", {})
        if operation == "translate" and "completion_tokens" in usage:
            return usage["completion_tokens"] * 0.20 / 10_000
        if operation.startswith("judge") and usage:
            cost = (usage.get("prompt_tokens", 0) * self.llm_input_price
                    + usage.get("completion_tokens", 0) * self.llm_output_price) / 1_000_000
            return cost
        return reservation

    def translate(self, text, language):
        # Conservative byte-based chunking stays far below the 32k token limit.
        from .media import text_chunks
        pieces = text_chunks(text, 1800)
        translated = []
        for piece in pieces:
            limit = min(4096, max(256, len(piece.encode()) * 3))
            payload = {"model": "indic-translate", "messages": [{"role": "user", "content": piece}],
                       "target_language_code": language, "max_output_tokens": limit}
            result = self.request("translate", payload, "https://api.bodhan.ai/v1/chat/completions",
                                  os.getenv("BODHAN_TRANSLATE_KEY"), limit * 0.20 / 10_000,
                                  payload=payload)
            if "choices" in result:
                try:
                    choice = result["choices"][0]
                    result = {"translation": choice["message"]["content"],
                              "truncated": choice.get("finish_reason") == "length"}
                except (KeyError, IndexError, TypeError):
                    raise Unavailable("Malformed Bodhan chat translation response") from None
            if result.get("truncated") or not isinstance(result.get("translation"), str):
                raise Unavailable("Bodhan translation is incomplete or malformed")
            translated.append(result["translation"])
        return "\n".join(translated)

    def speak(self, text, language, voice):
        from .media import text_chunks
        paths = []
        for piece in text_chunks(text):
            if not piece.strip():
                continue
            payload = {"model": "indic-speak", "input": piece, "voice": voice,
                       "instructions": json.dumps({"lang": language})}
            result = self.request("speak", payload, "https://api.bodhan.ai/v1/audio/speech",
                                  os.getenv("BODHAN_SPEAK_KEY"), len(piece) * 6 / 10_000,
                                  payload=payload)
            paths.append(result["audio_path"])
        return paths

    def transcribe(self, path, language, duration):
        contents = Path(path).read_bytes()
        identity = {"sha256": hashlib.sha256(contents).hexdigest(), "language": language,
                    "model": "indic-transcribe"}
        result = self.request("transcribe", identity, "https://api.bodhan.ai/v1/audio/transcriptions",
                              os.getenv("BODHAN_TRANSCRIBE_KEY"), duration * 0.10 / 60,
                              audio_path=path, language=language)
        if not isinstance(result.get("text"), str):
            raise Unavailable("Malformed transcription response")
        return result["text"]

    def judge(self, messages, repeat=0):
        if self.llm_input_price is None or self.llm_output_price is None:
            raise Unavailable("Set both judge token prices to enforce the shared rupee budget")
        model = os.getenv("LLM_MODEL", "")
        endpoint = os.getenv("LLM_BASE_URL", "").rstrip("/") + "/chat/completions"
        payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 1500}
        identity = {**payload, "independent_judgment": repeat}
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode()) + 128
        reservation = (input_bound * self.llm_input_price + 1500 * self.llm_output_price) / 1_000_000
        result = self.request(f"judge_{repeat}", identity, endpoint, os.getenv("LLM_API_KEY"),
                              reservation, payload=payload)
        try:
            content = result["choices"][0]["message"]["content"]
            if content.strip().startswith("```"):
                content = content.strip().split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, AttributeError):
            raise Unavailable("Judge returned malformed JSON; cached response preserved") from None
