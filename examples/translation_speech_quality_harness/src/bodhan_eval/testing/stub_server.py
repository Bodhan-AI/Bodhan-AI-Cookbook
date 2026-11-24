"""Local stand-in for the Bodhan endpoints and an OpenAI-compatible chat endpoint.

Why: tests (and offline demos) must exercise the *exact* CLI and the real HTTP clients,
with no test-only code paths in the harness. Point ``BODHAN_BASE_URL`` and
``LLM_BASE_URL`` at ``StubServer.url``, set any non-empty keys, and run as usual.

Endpoints and the request rules they enforce (from docs/dev/API_NOTES.md):
  POST /translate                  text + target_language; temperature/max_tokens/stream rejected
  POST /transliterate              text + language
  POST /v1/audio/speech            model "indic-speak", input, voice, instructions (JSON string with "lang")
  POST /v1/audio/transcriptions    multipart: file, model "indic-transcribe"; <= 30 s of audio
  POST .../chat/completions        OpenAI chat format: the judge LLM, or (model indic-translate /
                                   indic-transliterate) Bodhan's chat transport for the two text routes

Responses come from lookup tables in a fixture JSON:

    {
      "translate":     {"<target_language>": {"<text>": "<translation>" | {"text": ..., "truncated": true}}},
      "transliterate": {"<language>": {"<text>": "<rendering>"}},
      "transcribe":    {"<language>": "<transcript>" | {"tone_words": ["<word 0>", ...],
                        "errors": [{"min_s": 8, "max_s": 14, "drop_last_words": 3}]}},
      "chat":          [{"contains": "<substring of the last user message>", "content": "<assistant text>",
                         "starts_with": "<prefix>", "model": "<only this model>", "min_max_tokens": 3000,
                         "finish_reason": "length", "cost": 0.0012, "times": 2}],
      "chat_unsupported_formats": {"<model or *>": ["json_schema", "json_object"]},
      "faults":        [{"path": "/translate", "status": 429, "times": 2, "retry_after": "0.01"}]
    }

Chat rules are tried in order; the first whose optional conditions all hold answers. ``finish_reason:
"length"`` with empty content imitates a reasoning model that spent all of ``max_tokens`` thinking; ``cost``
is returned as ``usage.cost`` like OpenRouter does. A ``response_format`` type listed under
``chat_unsupported_formats`` is refused with HTTP 400, as some providers do.

Unmatched requests get deterministic defaults (translate/transliterate echo the text,
an empty transcript, a judge answer with no errors) and are recorded in ``misses``.
Speech synthesis returns a deterministic speech-like WAV whose length follows the text.
Faults fire on requests whose path ends with ``path`` (and whose body contains
``contains``, if given), ``times`` times (-1 = always).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

from .. import audio as A

SAMPLE_RATE = 24000
STUB_CHARS_PER_SECOND = 13.0  # matches the speaking-rate prior in langs.json
MAX_TRANSCRIBE_SECONDS = 30.0
REJECTED_TRANSLATE_FIELDS = ("temperature", "max_tokens", "stream")
# Bodhan text models on the chat-completions transport: model -> (fixture table, language field)
# request fields the judge client always sends; anything else in a chat request came from --llm-extra-body
OPENAI_CHAT_FIELDS = frozenset({"model", "messages", "temperature", "max_tokens", "response_format"})
BODHAN_CHAT_MODELS = {
    "indic-translate": ("translate", "target_language"),
    "indic-transliterate": ("transliterate", "language"),
}


def speech_like(seconds: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    """A deterministic voiced signal with syllable rhythm and short word gaps, framed by short silences.

    Harmonics of 140 Hz under a 4 Hz syllable envelope; 0.12 s gaps every 0.6 s. Peak stays near 0.45,
    so the waveform checks see clean, unclipped, tonal "speech" with no long silences.
    """
    n = max(1, int(seconds * sr))
    t = np.arange(n) / sr
    voiced = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in (1, 2, 3, 4))
    envelope = np.abs(np.sin(2 * np.pi * 4.0 * t)) ** 0.5
    envelope[(t % 0.6) > 0.48] = 0.0
    x = 0.22 * voiced * envelope
    pad = np.zeros(int(0.15 * sr))
    return np.concatenate([pad, x, pad]).astype(np.float32)


def speech_like_wav(text: str) -> bytes:
    return A.wav_bytes(speech_like(max(0.5, len(text) / STUB_CHARS_PER_SECOND)), SAMPLE_RATE)


# ---- tone-words: synthetic "speech" the stub can transcribe exactly -------------------------------------
# Word k of a test vocabulary is a pure tone at TONE_BASE_HZ + k * TONE_STEP_HZ. Tests build audio from word
# indices (dropping, repeating or garbling words, inserting silence or noise), and the stub's transcription
# decodes the tones it receives. So chunking, cut points, alignment and the second opinion are all exercised
# for real, and the transcript of each chunk depends on exactly what that chunk contains.
TONE_BASE_HZ = 300.0
TONE_STEP_HZ = 40.0


def tone_word(index: int, seconds: float = 0.25, sr: int = SAMPLE_RATE, amp: float = 0.4) -> np.ndarray:
    t = np.arange(max(1, int(seconds * sr))) / sr
    envelope = np.sin(np.pi * t / seconds) ** 0.5  # smooth start and end, no clicks
    return (amp * envelope * np.sin(2 * np.pi * (TONE_BASE_HZ + TONE_STEP_HZ * index) * t)).astype(np.float32)


def tone_speech(
    words: list[int], *, word_s: float = 0.25, gap_s: float = 0.1, edge_s: float = 0.15, sr: int = SAMPLE_RATE
) -> np.ndarray:
    """Tone-words separated by short gaps, framed by short silences."""
    edge = np.zeros(int(edge_s * sr), dtype=np.float32)
    gap = np.zeros(int(gap_s * sr), dtype=np.float32)
    parts = [edge]
    for k, w in enumerate(words):
        parts += [gap] if k else []
        parts.append(tone_word(w, word_s, sr))
    return np.concatenate([*parts, edge])


def decode_tone_words(x: np.ndarray, sr: int) -> list[int]:
    """Word indices of the tone-words in ``x``, in order. Broadband noise is not a word (a real ASR skips it too)."""
    db = A.frame_db(x, sr)
    if len(x) == 0 or not np.isfinite(db).any():
        return []
    thr = max(-60.0, float(np.percentile(db, 90)) - 30.0)
    hop = max(1, int(sr * A.FRAME_S))
    out = []
    for a, b in A.runs(db >= thr):
        if b - a < 2:
            continue
        seg = x[a * hop : b * hop] * np.hanning((b - a) * hop)
        mag = np.abs(np.fft.rfft(seg)) + 1e-10
        if np.exp(np.mean(np.log(mag))) / np.mean(mag) > 0.3:
            continue  # flat spectrum: noise
        hz = float(np.argmax(mag)) * sr / len(seg)
        out.append(round((hz - TONE_BASE_HZ) / TONE_STEP_HZ))
    return out


@dataclass
class Fault:
    path: str
    status: int
    times: int = 1
    retry_after: str | None = None
    code: str = "stub_fault"
    message: str = "injected by the stub server"
    contains: str | None = None
    fired: int = 0

    def matches(self, path: str, body: bytes) -> bool:
        if not path.endswith(self.path) or (self.times >= 0 and self.fired >= self.times):
            return False
        return self.contains is None or self.contains.encode("utf-8") in body


@dataclass
class StubState:
    fixture: dict[str, Any]
    faults: list[Fault]
    latency_s: float = 0.0
    requests: list[dict] = field(default_factory=list)  # {"path", "status", **details}
    misses: list[dict] = field(default_factory=list)  # requests answered by a default
    lock: threading.Lock = field(default_factory=threading.Lock)
    rule_uses: dict[int, int] = field(default_factory=dict)  # chat rule index -> times it answered

    def take_chat_rule(self, user: str, model: str, max_tokens: int) -> dict | None:
        """The first chat rule that matches and has answers left (a rule with ``times`` answers that often)."""
        with self.lock:
            for k, rule in enumerate(self.fixture.get("chat", [])):
                used = self.rule_uses.get(k, 0)
                if _chat_rule_matches(rule, user, model, max_tokens) and not (0 <= rule.get("times", -1) <= used):
                    self.rule_uses[k] = used + 1
                    return rule
        return None

    def log(self, path: str, status: int, **details: Any) -> None:
        with self.lock:
            self.requests.append({"path": path, "status": status, **details})

    def miss(self, kind: str, **details: Any) -> None:
        with self.lock:
            self.misses.append({"kind": kind, **details})

    def take_fault(self, path: str, body: bytes) -> Fault | None:
        with self.lock:
            for f in self.faults:
                if f.matches(path, body):
                    f.fired += 1
                    return f
        return None


class _ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details = details  # logged with the request, e.g. which model and format were refused


def _parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    """Form fields of a multipart/form-data body (the ``cgi`` module is gone in Python 3.13)."""
    head = f"Content-Type: {content_type}\r\n\r\n".encode()
    msg = BytesParser(policy=email_policy).parsebytes(head + body)
    fields: dict[str, bytes] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if isinstance(name, str):
            payload = part.get_payload(decode=True)
            fields[name] = payload if isinstance(payload, bytes) else b""
    return fields


class _Handler(BaseHTTPRequestHandler):
    server: _StubHTTPServer

    def log_message(self, format: str, *args: Any) -> None:  # silence per-request stderr lines
        return

    def do_GET(self) -> None:
        """``GET .../models``: the model listing with prices, as OpenRouter publishes it (fixture key ``models``)."""
        state = self.server.state
        path = self.path.split("?", 1)[0]
        if path.endswith("/models"):
            state.log(path, 200, method="GET")
            self._send(200, "application/json", _dumps({"data": state.fixture.get("models", [])}))
            return
        state.log(path, 404, method="GET")
        self._error(404, "not_found", f"no stub route for GET {path}")

    def do_POST(self) -> None:
        state = self.server.state
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        path = self.path.split("?", 1)[0]
        bearer = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()  # placeholder keys only
        if state.latency_s:
            time.sleep(state.latency_s)
        fault = state.take_fault(path, body)
        if fault is not None:
            state.log(path, fault.status, fault=True, bearer=bearer)
            headers = {"retry-after": fault.retry_after} if fault.retry_after else {}
            self._error(fault.status, fault.code, fault.message, headers)
            return
        try:
            status, ctype, payload, details = self._route(path, body)
        except _ApiError as e:
            state.log(path, e.status, error=e.code, bearer=bearer, **e.details)
            self._error(e.status, e.code, e.message)
            return
        state.log(path, status, bearer=bearer, **details)
        self._send(status, ctype, payload)

    # ---- routing -------------------------------------------------------------
    def _route(self, path: str, body: bytes) -> tuple[int, str, bytes, dict]:
        if path.endswith("/chat/completions"):
            return self._chat(body)
        if not self.headers.get("Authorization", "").removeprefix("Bearer ").strip():
            raise _ApiError(401, "invalid_api_key_error", "missing bearer key")
        if path == "/translate":
            return self._translate(_json(body))
        if path == "/transliterate":
            return self._transliterate(_json(body))
        if path == "/v1/audio/speech":
            return self._speech(_json(body))
        if path == "/v1/audio/transcriptions":
            return self._transcribe(_parse_multipart(self.headers.get("Content-Type", ""), body))
        raise _ApiError(404, "not_found", f"no stub route for {path}")

    def _translate(self, req: dict) -> tuple[int, str, bytes, dict]:
        bad = [k for k in REJECTED_TRANSLATE_FIELDS if k in req]
        if bad or not isinstance(req.get("text"), str) or not req.get("target_language"):
            raise _ApiError(400, "invalid_request_error", f"bad translate request (rejected fields: {bad})")
        text, lang = req["text"], str(req["target_language"])
        out, truncated = self._lookup("translate", lang, text)
        resp = {
            "translation": out,
            "target_language": lang,
            "script": "native",
            "truncated": truncated,
            "usage": {"completion_tokens": len(out) // 3 + 1},
        }
        return 200, "application/json", _dumps(resp), {"target_language": lang, "chars": len(text)}

    def _transliterate(self, req: dict) -> tuple[int, str, bytes, dict]:
        if not isinstance(req.get("text"), str) or not req.get("language"):
            raise _ApiError(400, "invalid_request_error", "bad transliterate request")
        out, truncated = self._lookup("transliterate", str(req["language"]), req["text"])
        resp = {"transliteration": out, "truncated": truncated, "usage": {"completion_tokens": len(out) // 3 + 1}}
        return 200, "application/json", _dumps(resp), {"language": req["language"]}

    def _speech(self, req: dict) -> tuple[int, str, bytes, dict]:
        if req.get("model") != "indic-speak" or not req.get("voice"):
            raise _ApiError(422, "invalid_request_error", "model must be indic-speak and voice is required")
        if not str(req.get("input") or "").strip():
            raise _ApiError(422, "invalid_request_error", "empty input")
        try:
            lang = json.loads(req.get("instructions") or "{}")["lang"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise _ApiError(422, "invalid_request_error", "instructions must be a JSON string with 'lang'") from e
        wav = speech_like_wav(req["input"])
        return 200, "audio/wav", wav, {"lang": lang, "voice": req["voice"], "chars": len(req["input"])}

    def _transcribe(self, form: dict[str, bytes]) -> tuple[int, str, bytes, dict]:
        if form.get("model", b"").decode() != "indic-transcribe" or "file" not in form:
            raise _ApiError(400, "invalid_request_error", "model must be indic-transcribe and file is required")
        x, sr = A.load_audio_bytes(form["file"])
        seconds = round(len(x) / sr, 3)
        if seconds > MAX_TRANSCRIBE_SECONDS:
            raise _ApiError(400, "invalid_request_error", f"audio is {seconds} s; the limit is 30 s per request")
        lang = form.get("language", b"").decode()
        table = self.server.state.fixture.get("transcribe", {})
        entry = table.get(lang)
        if entry is None:
            text = ""
            self.server.state.miss("transcribe", language=lang)
        elif isinstance(entry, dict):  # tone-word audio: decode what was actually sent (see tone_speech)
            words = [entry["tone_words"][i] for i in decode_tone_words(x, sr) if 0 <= i < len(entry["tone_words"])]
            for err in entry.get("errors", []):  # ASR mistakes that depend on the request's length
                if err["min_s"] <= seconds <= err["max_s"]:
                    words = words[: max(0, len(words) - int(err.get("drop_last_words", 0)))]
                    k = int(err.get("repeat_last_words", 0))
                    words = words + words[-k:] if k else words
            text = " ".join(words)
        else:
            text = str(entry)
        return 200, "application/json", _dumps({"text": text}), {"language": lang, "seconds": seconds}

    def _chat(self, body: bytes) -> tuple[int, str, bytes, dict]:
        req = _json(body)
        if req.get("model") in BODHAN_CHAT_MODELS:
            return self._bodhan_chat(req)
        model = str(req.get("model", "stub"))
        rf = req.get("response_format") or {}
        fmt = rf.get("type") if isinstance(rf, dict) else None
        unsupported = self.server.state.fixture.get("chat_unsupported_formats", {})
        if fmt and fmt in unsupported.get(model, unsupported.get("*", [])):
            raise _ApiError(
                400,
                "invalid_request_error",
                f"response_format {fmt} is not supported by {model}",
                model=model,
                response_format=fmt,
            )
        messages = req.get("messages") or []
        user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        max_tokens = int(req.get("max_tokens") or 0)
        rule = self.server.state.take_chat_rule(user, model, max_tokens)
        if rule is None:
            rule = {"content": '{"errors": []}'}
            self.server.state.miss("chat", model=model)
        content = str(rule["content"])
        prompt_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4
        # an empty answer cut by the length limit: a reasoning model that used all of max_tokens thinking
        completion_tokens = max_tokens if rule.get("finish_reason") == "length" else len(content) // 4
        usage: dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        if "cost" in rule:
            usage["cost"] = rule["cost"]  # OpenRouter-style provider-reported cost (USD)
        message = {"role": "assistant", "content": content}
        resp = {
            "id": "stub-chat",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": rule.get("finish_reason", "stop")}],
            "usage": usage,
        }
        details = {
            "model": model,
            "response_format": fmt,
            "json_schema": rf.get("json_schema") if fmt == "json_schema" else None,
            "max_tokens": max_tokens,
            "extra": {k: v for k, v in req.items() if k not in OPENAI_CHAT_FIELDS},
            "body_hash": hashlib.sha256(body).hexdigest(),
            "user": user,
        }
        return 200, "application/json", _dumps(resp), details

    def _bodhan_chat(self, req: dict) -> tuple[int, str, bytes, dict]:
        """The chat-completions transport of indic-translate / indic-transliterate (same tables as the routes)."""
        if not self.headers.get("Authorization", "").removeprefix("Bearer ").strip():
            raise _ApiError(401, "invalid_api_key_error", "missing bearer key")
        kind, lang_field = BODHAN_CHAT_MODELS[req["model"]]
        messages = req.get("messages") or []
        if len(messages) != 1 or messages[0].get("role") != "user" or not isinstance(messages[0].get("content"), str):
            raise _ApiError(400, "invalid_prompt", "exactly one user turn and no system turn")
        bad = [k for k in REJECTED_TRANSLATE_FIELDS if k in req]
        if bad or not req.get(lang_field):
            raise _ApiError(400, "invalid_request_error", f"bad {kind} request (rejected fields: {bad})")
        out, truncated = self._lookup(kind, str(req[lang_field]), messages[0]["content"])
        resp = {
            "id": "stub-bodhan-chat",
            "object": "chat.completion",
            "created": 0,
            "model": req["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": out},
                    "finish_reason": "length" if truncated else "stop",
                }
            ],
            "usage": {"completion_tokens": len(out) // 3 + 1},
        }
        return 200, "application/json", _dumps(resp), {"model": req["model"], lang_field: req[lang_field]}

    def _lookup(self, kind: str, lang: str, text: str) -> tuple[str, bool]:
        """(output, truncated). A table value is the output, or {"text": ..., "truncated": true}."""
        table = self.server.state.fixture.get(kind, {}).get(lang, {})
        if text not in table:
            self.server.state.miss(kind, language=lang, text=text)
            return text, False
        value = table[text]
        if isinstance(value, dict):
            return str(value.get("text", "")), bool(value.get("truncated", False))
        return str(value), False

    # ---- responses -----------------------------------------------------------
    def _send(self, status: int, ctype: str, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, code: str, message: str, headers: dict[str, str] | None = None) -> None:
        envelope = {"error": {"message": message, "code": code, "request_id": "stub"}}
        self._send(status, "application/json", _dumps(envelope), headers)


def _chat_rule_matches(rule: dict, user: str, model: str, max_tokens: int) -> bool:
    return (
        rule.get("contains", "") in user
        and user.startswith(rule.get("starts_with", ""))
        and rule.get("model", model) == model
        and max_tokens >= int(rule.get("min_max_tokens", 0))
    )


def _json(body: bytes) -> dict:
    try:
        obj = json.loads(body or b"{}")
    except json.JSONDecodeError as e:
        raise _ApiError(400, "invalid_request_error", "body is not JSON") from e
    if not isinstance(obj, dict):
        raise _ApiError(400, "invalid_request_error", "body is not a JSON object")
    return obj


def _dumps(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")


class _StubHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], state: StubState):
        super().__init__(addr, _Handler)
        self.state = state


class StubServer:
    """Run the stub on 127.0.0.1 in a background thread. Use as a context manager."""

    def __init__(self, fixture: dict | str | Path | None = None, port: int = 0, latency_s: float = 0.0):
        if isinstance(fixture, (str, Path)):
            table: dict = json.loads(Path(fixture).read_text(encoding="utf-8"))
        else:
            table = dict(fixture or {})
        faults = [Fault(**f) for f in table.pop("faults", [])]
        self.state = StubState(fixture=table, faults=faults, latency_s=latency_s)
        self._httpd = _StubHTTPServer(("127.0.0.1", port), self.state)
        # a short poll interval keeps shutdown() fast (the default 0.5 s adds up across many tests)
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host!s}:{port}"

    @property
    def requests(self) -> list[dict]:
        return self.state.requests

    @property
    def misses(self) -> list[dict]:
        return self.state.misses

    def add_fault(self, path: str, status: int, **kw: Any) -> Fault:
        f = Fault(path=path, status=status, **kw)
        with self.state.lock:
            self.state.faults.append(f)
        return f

    def env(self) -> dict[str, str]:
        """Environment variables that point the harness at this stub (placeholder keys, never real ones).

        Only the three documented Bodhan keys, as the graders set them; transliteration falls back to the
        translate key.
        """
        return {
            "BODHAN_BASE_URL": self.url,
            "BODHAN_TRANSLATE_KEY": "stub-key",
            "BODHAN_SPEAK_KEY": "stub-key",
            "BODHAN_TRANSCRIBE_KEY": "stub-key",
            "LLM_BASE_URL": f"{self.url}/v1",
            "LLM_MODEL": "stub-judge",
            "LLM_API_KEY": "stub-key",
        }

    def start(self) -> StubServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> StubServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Run the offline stub for the Bodhan and LLM endpoints.")
    ap.add_argument("--fixture", type=Path, default=None, help="lookup-table JSON (see module docstring)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--latency-ms", type=float, default=0.0, help="simulated latency per request")
    args = ap.parse_args(argv)
    with StubServer(args.fixture, port=args.port, latency_s=args.latency_ms / 1000) as stub:
        for k, v in stub.env().items():
            print(f"{k}={v}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
