"""Bodhan-only media adapters and a cached OpenAI-compatible judge client.

No model endpoint or credential is inferred from the dataset. Cache identities
contain exact request bytes and model settings, never authentication headers.
"""

import base64
import hashlib
import io
import json
import math
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import wave
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

BODHAN = "https://api.bodhan.ai"
VOICES = dict(
    zip(
        "as bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur".split(),
        (
            "Prastuti Ishita Gwrbw Preeti Amit Dhara Kavya Deepika Anjali Zoon Vaidehi Lakshmi "
            "Thoibi Anagha Srijana Itishree Kaur Bharati Phulmani Moomal Anitha Sravani Saba"
        ).split(),
    )
)


def load_environment(path):
    """Read simple dotenv assignments locally; the process environment wins."""
    environment = {}
    path = Path(path)
    if path.exists():
        for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                raise ValueError(f"Invalid environment assignment on line {line_no}")
            name, value = line.split("=", 1)
            name, value = name.strip(), value.strip()
            if name not in {"BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"}:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            environment[name] = value
    environment.update(os.environ)
    return environment


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ModelError(Exception):
    """Safe model failure message, without response bodies or secrets."""


class Budget:
    def __init__(self, maximum):
        if not math.isfinite(maximum) or maximum < 0:
            raise ValueError("budget must be finite and nonnegative")
        self.maximum = maximum
        self.reserved = 0.0
        self.lock = threading.Lock()

    def reserve(self, amount):
        with self.lock:
            if amount < 0 or not math.isfinite(amount) or self.reserved + amount > self.maximum + 1e-12:
                raise ModelError("budget_exhausted")
            self.reserved += amount


def transport(url, headers, body):
    if os.name == "nt" and urlsplit(url).hostname == "api.groq.com":
        return groq_transport(url, headers, body)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def groq_transport(url, headers, body):
    # Supply credentials and request text over stdin. Disable local curl config
    # and redirects so neither can forward the authorization header.
    if url != "https://api.groq.com/openai/v1/chat/completions":
        raise ModelError("unexpected_groq_endpoint")
    config = [f"header = {json.dumps(f'{name}: {value}', ensure_ascii=False)}" for name, value in headers.items()]
    config.append("data-binary = " + json.dumps(body.decode("utf-8"), ensure_ascii=False))
    try:
        result = subprocess.run(
            ["curl.exe", "-q", "--config", "-", "--silent", "--max-time", "90", "--write-out", "\n%{http_code}", url],
            input=("\n".join(config) + "\n").encode("utf-8"),
            capture_output=True,
            timeout=95,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("Groq request timed out") from None
    if result.returncode:
        raise OSError("Groq curl request failed")
    response, status = result.stdout.rsplit(b"\n", 1)
    code = int(status)
    if not 200 <= code < 300:
        raise urllib.error.HTTPError(url, code, "Groq request failed", {}, None)
    return response


class Client:
    def __init__(self, cache, budget, concurrency=3, retries=2, send=None, sleep=time.sleep, env=None):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.budget = Budget(budget)
        self.semaphore = threading.BoundedSemaphore(concurrency)
        self.retries = retries
        self.send = send or transport
        self.sleep = sleep
        self.env = dict(os.environ if env is None else env)
        self.lock = threading.Lock()
        self.key_locks = {}
        self.calls = 0
        self.hits = 0
        self.records = {}
        self.local = threading.local()

    @contextmanager
    def capture(self):
        """Trace successful cache identities for a generation operation, including hits."""
        previous = getattr(self.local, "capture", None)
        captured = []
        self.local.capture = captured
        try:
            yield captured
        finally:
            self.local.capture = previous

    def request(self, url, model, payload, key_name, reservation, raw_body=None, content_type="application/json", binary=False, replicate=0):
        identity = {"version": 1, "url": url, "model": model, "payload": payload, "content_type": content_type, "binary": binary, "replicate": replicate}
        cache_key = digest(canonical(identity).encode())
        with self.lock:
            lock = self.key_locks.setdefault(cache_key, threading.Lock())
        with lock:
            path = self.cache / f"{cache_key}.json"
            if path.exists():
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if record["identity"] != identity:
                        raise ValueError("identity mismatch")
                    response = base64.b64decode(record["response"], validate=True)
                    if digest(response) != record["response_sha256"]:
                        raise ValueError("response mismatch")
                except (ValueError, KeyError, TypeError) as exc:
                    raise ModelError("cache_corrupt; remove the affected entry") from exc
                with self.lock:
                    self.hits += 1
            else:
                key = self.env.get(key_name)
                if not key:
                    raise ModelError(f"missing_configuration:{key_name}")
                body = raw_body if raw_body is not None else canonical(payload).encode()
                headers = {"Authorization": f"Bearer {key}", "Content-Type": content_type}
                response = None
                for attempt in range(self.retries + 1):
                    # Every attempt reserves its full upper bound. Ambiguous failed
                    # requests may have been billed; never refund their reservation.
                    self.budget.reserve(reservation)
                    with self.semaphore:
                        with self.lock:
                            self.calls += 1
                        try:
                            response = self.send(url, headers, body)
                            break
                        except urllib.error.HTTPError as exc:
                            if exc.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                                raise ModelError(f"http_error:{exc.code}") from None
                        except (urllib.error.URLError, TimeoutError, OSError) as exc:
                            reason = getattr(exc, "reason", exc)
                            if isinstance(reason, PermissionError):
                                raise ModelError("network_access_denied") from None
                            if attempt == self.retries:
                                raise ModelError("transport_failed") from None
                    self.sleep(min(8, 2**attempt))
                if response is None:
                    raise ModelError("empty_response")
                record = {
                    "identity": identity,
                    "response": base64.b64encode(response).decode(),
                    "response_sha256": digest(response),
                    "reservation_inr": reservation,
                }
                temporary = path.with_suffix(".tmp")
                temporary.write_text(canonical(record) + "\n", encoding="utf-8")
                temporary.replace(path)
            with self.lock:
                self.records[cache_key] = {"model": model, "reservation_inr": record["reservation_inr"]}
            captured = getattr(self.local, "capture", None)
            if captured is not None:
                captured.append(cache_key)
            if binary:
                return response
            try:
                result = json.loads(response)
                if not isinstance(result, dict) or "error" in result:
                    raise ValueError("invalid response")
                return result
            except (ValueError, TypeError):
                raise ModelError("invalid_model_response") from None

    def translate(self, text, lang, source_lang="en"):
        payload = {"text": text, "target_language": lang, "max_output_tokens": 4096}
        rate = 4096 * 0.20 / 10000
        chat_payload = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "source_language_code": source_lang,
            "target_language_code": lang,
            "max_output_tokens": 4096,
        }
        chat_url = BODHAN + "/v1/chat/completions"
        identity = {
            "version": 1,
            "url": chat_url,
            "model": "indic-translate",
            "payload": chat_payload,
            "content_type": "application/json",
            "binary": False,
            "replicate": 0,
        }
        try:
            if (self.cache / f"{digest(canonical(identity).encode())}.json").exists():
                raise ModelError("cached_translation_fallback")
            result = self.request(BODHAN + "/translate", "indic-translate", payload, "BODHAN_TRANSLATE_KEY", rate)
        except ModelError as exc:
            if str(exc) not in {"http_error:404", "cached_translation_fallback"}:
                raise
            reply = self.request(chat_url, "indic-translate", chat_payload, "BODHAN_TRANSLATE_KEY", rate)
            try:
                choice = reply["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError
                result = {"translation": choice["message"]["content"]}
            except (KeyError, TypeError, IndexError, AttributeError, ValueError):
                raise ModelError("invalid_or_incomplete_translation_response") from None
        if result.get("truncated") is True:
            raise ModelError("translation_truncated")
        if not isinstance(result.get("translation"), str) or not result["translation"].strip():
            raise ModelError("missing_translation")
        return result["translation"]

    def speak(self, text, lang):
        parts = []
        # Split on character offsets without asking a model to rewrite the input.
        start = 0
        while start < len(text):
            end = min(len(text), start + 160)
            if end < len(text):
                boundary = text.rfind(" ", start + 60, end)
                if boundary > start:
                    end = boundary + 1
            chunk = text[start:end]
            payload = {"model": "indic-speak", "input": chunk, "voice": VOICES[lang], "instructions": canonical({"lang": lang})}
            data = self.request(BODHAN + "/v1/audio/speech", "indic-speak", payload, "BODHAN_SPEAK_KEY", len(chunk) * 6 / 10000, binary=True)
            parts.append(data)
            start = end
        try:
            return join_wav(parts)
        except (wave.Error, EOFError, ValueError):
            raise ModelError("invalid_synthesis_wav") from None

    def transcribe(self, data, lang, replicate=0):
        try:
            chunks = split_wav(data)
        except (wave.Error, EOFError, ValueError):
            raise ModelError("ASR requires readable PCM WAV; convert other formats with ffmpeg") from None
        texts = []
        for chunk, duration in chunks:
            payload = {"model": "indic-transcribe", "language": lang, "audio_sha256": digest(chunk)}
            boundary = "bodhan_eval_" + digest(chunk)[:24]
            body = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nindic-transcribe\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{lang}\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                    "Content-Type: audio/wav\r\n\r\n"
                ).encode()
                + chunk
                + f"\r\n--{boundary}--\r\n".encode()
            )
            response = self.request(
                BODHAN + "/v1/audio/transcriptions",
                "indic-transcribe",
                payload,
                "BODHAN_TRANSCRIBE_KEY",
                duration * 0.1 / 60,
                raw_body=body,
                content_type=f"multipart/form-data; boundary={boundary}",
                replicate=replicate,
            )
            if not isinstance(response.get("text"), str):
                raise ModelError("missing_transcript")
            texts.append(response["text"])
        return " ".join(texts)

    def judge(self, messages, rates, repetition=0):
        base = self.env.get("LLM_BASE_URL", "").rstrip("/")
        model = self.env.get("LLM_MODEL", "")
        if not base or not model:
            raise ModelError("missing_configuration:LLM_BASE_URL/LLM_MODEL")
        # Replicates use identical HTTP inputs but separately cached assessments.
        # This tests consistency without changing the rubric or source prompt.
        payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 2048}
        if urlsplit(base).hostname == "api.groq.com":
            payload["response_format"] = {"type": "json_object"}
        if rates is None:
            raise ModelError("judge_prices_required: set both --llm-*-inr-per-million (0 for a free endpoint)")
        upper_input_tokens = len(canonical(payload).encode("utf-8")) + 256
        reservation = (upper_input_tokens * rates[0] + 2048 * rates[1]) / 1000000
        result = self.request(base + "/chat/completions", model, payload, "LLM_API_KEY", reservation, replicate=repetition)
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") not in (None, "stop"):
                raise ValueError("truncated judge output")
            return json.loads(choice["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("invalid_judge_json") from None


def split_wav(data, seconds=25):
    chunks = []
    with wave.open(io.BytesIO(data), "rb") as audio:
        params = audio.getparams()
        if params.comptype != "NONE" or params.nframes == 0:
            raise ValueError("empty or compressed WAV")
        while True:
            frames = audio.readframes(params.framerate * seconds)
            if not frames:
                break
            output = io.BytesIO()
            with wave.open(output, "wb") as segment:
                segment.setparams(params)
                segment.writeframes(frames)
            duration = len(frames) / params.sampwidth / params.nchannels / params.framerate
            chunks.append((output.getvalue(), duration))
    return chunks


def join_wav(parts):
    output = io.BytesIO()
    expected = None
    with wave.open(output, "wb") as result:
        for data in parts:
            with wave.open(io.BytesIO(data), "rb") as audio:
                params = audio.getparams()
                signature = (params.nchannels, params.sampwidth, params.framerate, params.comptype)
                if expected is None:
                    expected = signature
                    result.setparams(params)
                if signature != expected or params.nframes == 0:
                    raise ValueError("incompatible audio chunks")
                result.writeframes(audio.readframes(params.nframes))
    return output.getvalue()
