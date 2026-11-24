"""Shared fixtures: fake env, a scriptable fake Bodhan backend, and generated media."""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import threading
import wave
from dataclasses import dataclass, field
from typing import Any

import httpx
import pymupdf
import pytest

from bodhan_mcp.core import client

KEYS = {
    "BODHAN_TRANSCRIBE_KEY": "test-key-asr-not-real",
    "BODHAN_TRANSLATE_KEY": "test-key-mt-not-real",
    "BODHAN_SPEAK_KEY": "test-key-tts-not-real",
    "BODHAN_OCR_KEY": "test-key-ocr-not-real",
}
_KEY_NAME = {v: k for k, v in KEYS.items()}


@pytest.fixture(autouse=True)
def bodhan_env(monkeypatch, tmp_path):
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    for k in ("BODHAN_API_KEY", "BODHAN_MAX_CONCURRENCY", "BODHAN_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BODHAN_BASE_URL", "https://fake.bodhan.test")
    monkeypatch.setenv("BODHAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    yield
    client.set_transport(None)


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(client, "_sleep", calls.append)
    return calls


@dataclass
class Req:
    path: str
    kind: str
    model: str | None
    key_var: str | None
    size: int
    json: Any = None
    fingerprint: str = ""
    filename: str | None = None
    mime: str | None = None
    file_bytes: bytes = b""
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def input_len(self) -> int:
        return len((self.json or {}).get("input", ""))

    @property
    def wav_seconds(self) -> float:
        with wave.open(io.BytesIO(self.file_bytes)) as w:
            return w.getnframes() / w.getframerate()


def wav_bytes(seconds: float, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(2 * int(rate * seconds)))
    return buf.getvalue()


def to_mp3_bytes(wav: bytes) -> bytes:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "wav", "-i", "pipe:0", "-f", "mp3", "pipe:1"],
        input=wav, capture_output=True, check=True,
    )
    return p.stdout


class FakeBodhan:
    """Scriptable backend. Queue items (per kind) are consumed one per request:

    - httpx.Response -> returned as is (use `error()`); Exception -> raised; callable(req) -> response
    - asr: str is the transcript text; dict is a raw 200 body
    - mt: dict(text=, tokens=, finish=); ocr: dict(content=, blocks=); tts: bytes
    """

    def __init__(self) -> None:
        self.requests: list[Req] = []
        self.queues: dict[str, list[Any]] = {"asr": [], "mt": [], "ocr": [], "tts": []}
        self.counts = {"asr": 0, "mt": 0, "ocr": 0, "tts": 0}
        self.asr_fn = None
        self.mt_fn = None
        self.tts_mp3 = False
        self.tts_factor = 1.0
        self.tts_factors: list[float] = []
        self._lock = threading.Lock()

    def script(self, kind: str, *items: Any) -> None:
        self.queues[kind].extend(items)

    @staticmethod
    def error(status: int, body: Any = None, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {"error": {"message": "scripted"}}, headers=headers)

    def of(self, kind: str) -> list[Req]:
        return [r for r in self.requests if r.kind == kind]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        rec = self._record(request)
        if rec.kind == "other":
            return self.error(404, {"error": {"message": "not found"}})
        with self._lock:
            n = self.counts[rec.kind]
            self.counts[rec.kind] += 1
            queue = self.queues[rec.kind]
            item = queue.pop(0) if queue else None
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        if callable(item):
            return item(rec)
        return getattr(self, "_" + rec.kind)(rec, item, n)

    def _record(self, request: httpx.Request) -> Req:
        path = request.url.path
        body = request.content
        auth = request.headers.get("authorization", "").removeprefix("Bearer ")
        rec = Req(path, "other", None, _KEY_NAME.get(auth), len(body))
        ctype = request.headers.get("content-type", "")
        if path == "/v1/audio/transcriptions":
            rec.kind, rec.model = "asr", "indic-transcribe"
            m = re.search(r"boundary=(\S+)", ctype)
            for part in body.split(b"--" + m.group(1).encode())[1:-1]:
                head, _, payload = part.partition(b"\r\n\r\n")
                payload = payload[:-2]
                name = re.search(rb'name="([^"]+)"', head).group(1).decode()
                if name == "file":
                    rec.filename = re.search(rb'filename="([^"]+)"', head).group(1).decode()
                    rec.mime = re.search(rb"Content-Type: (\S+)", head).group(1).decode()
                    rec.file_bytes = payload
                    rec.fingerprint = hashlib.sha256(payload).hexdigest()
                else:
                    rec.fields[name] = payload.decode()
        elif path in ("/v1/chat/completions", "/v1/audio/speech"):
            rec.json = json.loads(body)
            rec.model = rec.json.get("model")
            rec.fingerprint = hashlib.sha256(body).hexdigest()
            rec.kind = {"indic-translate": "mt", "indic-ocr": "ocr", "indic-speak": "tts"}.get(rec.model, "other")
        with self._lock:
            self.requests.append(rec)
        return rec

    def _asr(self, rec: Req, item: Any, n: int) -> httpx.Response:
        if isinstance(item, dict):
            return httpx.Response(200, json=item)
        if item is None:
            item = self.asr_fn(rec) if self.asr_fn else f"transcribed window {n}"
        return httpx.Response(200, json={"text": item})

    def _mt(self, rec: Req, item: Any, n: int) -> httpx.Response:
        item = dict(item or {})
        text = item.get("text")
        if text is None:
            src = rec.json["messages"][0]["content"]
            text = self.mt_fn(rec) if self.mt_fn else src
        tokens = item.get("tokens", max(len(text) // 2, 1))
        body = {
            "choices": [{"message": {"content": text}, "finish_reason": item.get("finish", "stop")}],
            "usage": {"completion_tokens": tokens},
        }
        return httpx.Response(200, json=body)

    def _ocr(self, rec: Req, item: Any, n: int) -> httpx.Response:
        item = dict(item or {})
        blocks = item.get("blocks")
        if blocks is None:
            blocks = [
                {"order": 1, "label": "Text", "conf": 0.9, "bbox_xyxy": [0, 10, 5, 20], "text": f"Body of ocr call {n + 1}."},
                {"order": 0, "label": "Section-title", "conf": 0.95, "bbox_xyxy": [0, 0, 5, 9], "text": f"Heading {n + 1}"},
            ]
        content = item.get("content", "\n\n".join(b["text"] for b in blocks))
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "blocks": blocks})

    def _tts(self, rec: Req, item: Any, n: int) -> httpx.Response:
        if isinstance(item, bytes):
            return httpx.Response(200, content=item)
        factor = self.tts_factors.pop(0) if self.tts_factors else self.tts_factor
        audio = wav_bytes(len(rec.json["input"]) / 13.5 * factor)
        if self.tts_mp3:
            return httpx.Response(200, content=to_mp3_bytes(audio), headers={"content-type": "audio/mpeg"})
        return httpx.Response(200, content=audio, headers={"content-type": "audio/wav"})


@pytest.fixture
def bodhan():
    fake = FakeBodhan()
    client.set_transport(httpx.MockTransport(fake))
    return fake


@pytest.fixture
def make_audio(tmp_path):
    """make_audio("a.wav", seconds, gap_at=25.0) -> path; tone with an optional 0.6 s silence gap."""

    def make(name: str, seconds: float, gap_at: float | None = None):
        path = tmp_path / name
        expr = "0.3*sin(2*PI*440*t)"
        if gap_at is not None:
            expr += f"*(1-between(t\\,{gap_at - 0.3}\\,{gap_at + 0.3}))"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=16000:d={seconds}", str(path)],
            check=True,
        )
        return path

    return make


@pytest.fixture
def make_pdf(tmp_path):
    """make_pdf("d.pdf", pages) -> path; every page carries distinct text so cache keys differ."""

    def make(name: str, pages: int):
        path = tmp_path / name
        doc = pymupdf.open()
        for i in range(pages):
            doc.new_page().insert_text((72, 100), f"Test page number {i + 1}", fontsize=28)
        doc.save(str(path))
        doc.close()
        return path

    return make
