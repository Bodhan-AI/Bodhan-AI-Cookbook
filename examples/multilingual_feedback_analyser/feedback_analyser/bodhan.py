"""Thin client for the five Bodhan models: retries, caching, readable errors."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import random
import re
import threading
import time
from collections import Counter
from pathlib import Path

import requests

from .audio import is_wav, split_wav, wav_seconds
from .cache import NoCache, cache_key

DEFAULT_BASE_URL = "https://api.bodhan.ai"
TIMEOUT = (10, 180)
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
MAX_RETRY_AFTER = 30.0
MAX_CLIP_SECONDS = 28.0
CHUNK_SECONDS = 25.0
MODEL_KEY_VARS = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_API_KEY",
    "indic-ocr": "BODHAN_OCR_API_KEY",
    "indic-translate": "BODHAN_TRANSLATE_API_KEY",
    "indic-transliterate": "BODHAN_TRANSLITERATE_API_KEY",
    "indic-speak": "BODHAN_SPEAK_API_KEY",
}
TABLE_RULE = re.compile(r"^[\s|:-]+$", re.MULTILINE)
MARKUP = re.compile(r"<[^>]+>|[#*_`|>]+")


class BodhanError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.status:
            parts.append(f"status={self.status}")
        if self.code:
            parts.append(f"code={self.code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " ".join(parts)


def _error_from(response: requests.Response) -> BodhanError:
    code = None
    request_id = response.headers.get("x-request-id")
    message = (response.text or "")[:300]
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        problem = data.get("error")
        if isinstance(problem, dict):
            message = str(problem.get("message") or message)
            code = problem.get("code")
            request_id = problem.get("request_id") or request_id
        elif "detail" in data:
            message = str(data["detail"])
    return BodhanError(message, status=response.status_code, code=str(code) if code else None, request_id=request_id)


def markdown_to_text(markdown: str) -> str:
    text = MARKUP.sub(" ", TABLE_RULE.sub("", markdown))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


class BodhanClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, cache=None, sleep=time.sleep, session_factory=requests.Session):
        self.api_key = api_key or os.environ.get("BODHAN_API_KEY", "")
        self.model_keys = {} if api_key else {model: os.environ.get(name, "") for model, name in MODEL_KEY_VARS.items()}
        if not self.api_key and not any(self.model_keys.values()):
            raise BodhanError("BODHAN_API_KEY is not set. Copy .env.example to .env and add your key.")
        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.cache = cache or NoCache()
        self.sleep = sleep
        self.calls: Counter[str] = Counter()
        self.cache_hits: Counter[str] = Counter()
        self._session_factory = session_factory
        self._local = threading.local()
        self._lock = threading.Lock()
        self._plain_routes = True

    def _session(self):
        if not hasattr(self._local, "session"):
            self._local.session = self._session_factory()
        return self._local.session

    def _key_for(self, model: str) -> str:
        key = self.model_keys.get(model) or self.api_key
        if not key:
            raise BodhanError(f"no key for {model}: set {MODEL_KEY_VARS[model]} or BODHAN_API_KEY in .env")
        return key

    def _post(self, model: str, path: str, **kwargs) -> requests.Response:
        url = self.base_url + path
        headers = {"Authorization": f"Bearer {self._key_for(model)}"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last = attempt == MAX_ATTEMPTS
            try:
                response = self._session().post(url, headers=headers, timeout=TIMEOUT, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as problem:
                if last:
                    raise BodhanError(f"could not reach {url}: {type(problem).__name__}") from problem
                self.sleep(BACKOFF_SECONDS[attempt - 1] + random.uniform(0, 0.5))
                continue
            if response.status_code == 200:
                with self._lock:
                    self.calls[model] += 1
                return response
            if (response.status_code == 429 or response.status_code >= 500) and not last:
                self.sleep(self._wait(response, attempt))
                continue
            raise _error_from(response)
        raise BodhanError("retries exhausted")

    @staticmethod
    def _wait(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), MAX_RETRY_AFTER)
            except ValueError:
                pass
        return BACKOFF_SECONDS[attempt - 1] + random.uniform(0, 0.5)

    def _cached(self, model: str, key: str, compute) -> bytes:
        hit = self.cache.get(key)
        if hit is not None:
            with self._lock:
                self.cache_hits[model] += 1
            return hit
        value = compute()
        self.cache.put(key, value)
        return value

    def _chat_text(self, model: str, body: dict) -> str:
        response = self._post(model, "/v1/chat/completions", json=body)
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as problem:
            raise BodhanError(f"{model} returned a reply that could not be read", status=response.status_code) from problem
        if choice.get("finish_reason") == "length":
            raise BodhanError(f"{model} output was cut off by the token limit; split the input into shorter pieces")
        return str(content).strip()

    def _plain_or_chat(self, model: str, path: str, plain_body: dict, field: str, chat_body: dict) -> str:
        if self._plain_routes:
            try:
                data = self._post(model, path, json=plain_body).json()
                if data.get("truncated"):
                    raise BodhanError(f"{model} output was cut off by the token limit; split the input into shorter pieces")
                return str(data[field]).strip()
            except BodhanError as problem:
                if problem.status != 404:
                    raise
                self._plain_routes = False
        return self._chat_text(model, chat_body)

    def translate(self, text: str, target_language: str, script: str | None = None) -> str:
        if not text.strip():
            return ""

        def compute() -> bytes:
            plain = {"text": text, "target_language": target_language}
            chat = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], "target_language": target_language}
            if script:
                plain["script"] = script
                chat["script"] = script
            return self._plain_or_chat("indic-translate", "/translate", plain, "translation", chat).encode("utf-8")

        return self._cached("indic-translate", cache_key("translate", text, target_language, script), compute).decode("utf-8")

    def transliterate(self, text: str, language: str, script: str = "roman") -> str:
        if not text.strip():
            return ""

        def compute() -> bytes:
            plain = {"text": text, "language": language, "script": script}
            chat = {"model": "indic-transliterate", "messages": [{"role": "user", "content": text}], "target_language": language, "script": script}
            return self._plain_or_chat("indic-transliterate", "/transliterate", plain, "transliteration", chat).encode("utf-8")

        return self._cached("indic-transliterate", cache_key("transliterate", text, language, script), compute).decode("utf-8")

    def transcribe(self, path: str | Path, language: str | None = None) -> str:
        blob = Path(path).read_bytes()

        def compute() -> bytes:
            clips = [blob]
            if is_wav(blob) and wav_seconds(blob) > MAX_CLIP_SECONDS:
                clips = split_wav(blob, CHUNK_SECONDS)
            mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            parts = []
            for index, clip in enumerate(clips):
                data = {"model": "indic-transcribe"}
                if language:
                    data["language"] = language
                files = {"file": (f"{index}_{Path(path).name}", clip, mime)}
                response = self._post("indic-transcribe", "/v1/audio/transcriptions", files=files, data=data)
                try:
                    parts.append(str(response.json()["text"]).strip())
                except (ValueError, KeyError) as problem:
                    raise BodhanError("indic-transcribe returned a reply that could not be read") from problem
            return " ".join(part for part in parts if part).encode("utf-8")

        return self._cached("indic-transcribe", cache_key("transcribe", blob, language), compute).decode("utf-8")

    def ocr(self, path: str | Path) -> dict:
        blob = Path(path).read_bytes()

        def compute() -> bytes:
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            url = f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"
            body = {
                "model": "indic-ocr",
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}],
                "table_format": "markdown",
            }
            response = self._post("indic-ocr", "/v1/chat/completions", json=body)
            try:
                data = response.json()
                markdown = str(data["choices"][0]["message"]["content"])
            except (ValueError, KeyError, IndexError, TypeError) as problem:
                raise BodhanError("indic-ocr returned a reply that could not be read") from problem
            scores = [block["conf"] for block in data.get("blocks") or [] if isinstance(block.get("conf"), (int, float))]
            confidence = round(sum(scores) / len(scores), 3) if scores else None
            return json.dumps({"text": markdown_to_text(markdown), "confidence": confidence}, ensure_ascii=False).encode("utf-8")

        return json.loads(self._cached("indic-ocr", cache_key("ocr", blob), compute))

    def speak(self, text: str, voice: str, lang: str, style: str | None = None) -> bytes:
        def compute() -> bytes:
            instructions = {"lang": lang}
            if style:
                instructions["style"] = style
            body = {"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instructions)}
            response = self._post("indic-speak", "/v1/audio/speech", json=body)
            if not is_wav(response.content):
                raise BodhanError("indic-speak did not return WAV audio", status=response.status_code)
            return response.content

        return self._cached("indic-speak", cache_key("speak", text, voice, lang, style), compute)
