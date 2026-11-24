"""Shared fixtures: isolated work/cache dirs, fake keys, a mocked Bodhan HTTP layer, and audio/PDF builders."""
from __future__ import annotations

import io
import json
import math
import shutil
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from bodhan_mcp import config
from bodhan_mcp.client import client

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAVE_POPPLER = bool(shutil.which("pdftoppm"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
needs_poppler = pytest.mark.skipif(not HAVE_POPPLER, reason="poppler (pdftoppm) not installed")

HINDI = "नमस्ते आज हम गणित की कक्षा शुरू करेंगे।"
TAMIL = "வணக்கம் இன்று வகுப்பு பத்து மணிக்கு."


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_WORKDIR", str(tmp_path / "work"))
    monkeypatch.setenv("BODHAN_CACHE_DIR", str(tmp_path / "cache"))
    for name in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY",
                 "INDIC_TRANSCRIBE_API_KEY", "INDIC_TRANSLATE", "INDIC_SPEAK", "INDIC_OCR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "_ENV_LOADED", True)  # never read the developer's real .env in tests
    for name in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(name, "test-key-" + name)
    client.sleep = lambda _s: None
    yield
    client.configure_for_tests(None)


def tone_wav(path: Path, parts: list[tuple[float, bool]], rate: int = 16000) -> Path:
    """parts = [(seconds, is_tone)]: tone bursts separated by digital silence."""
    frames = bytearray()
    for dur, tone in parts:
        n = int(dur * rate)
        if tone:
            frames += b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * i / rate))) for i in range(n))
        else:
            frames += b"\x00\x00" * n
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def wav_bytes(seconds: float = 1.0, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


def multipart_wav_seconds(content: bytes) -> float:
    idx = content.index(b"RIFF")
    with wave.open(io.BytesIO(content[idx:]), "rb") as w:
        return w.getnframes() / w.getframerate()


class MockBodhan:
    """Records every request; answers like the real API. Use `.calls` to assert on what was (not) sent."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail_first: dict[str, list[int]] = {}
        self.translate_map = {"hi": HINDI, "ta": TAMIL, "en": "Hello, today we will start our maths class.", "mr": "नमस्कार, आज आपण गणिताचा वर्ग सुरू करू."}
        self.transcript = HINDI
        self.ocr_text = "# Title\n\nSome page text 12\n\n| A | B |\n|---|---|\n| x | 5 |"
        self.ocr_conf = 0.9
        self.translate_fn = None  # optional (text, target) -> str override
        self.stall_speech_over: int | None = None  # simulate a runaway generation for inputs longer than this

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        entry = {"path": path, "bytes": len(request.content), "auth": request.headers.get("authorization", "")}
        if path == "/v1/audio/transcriptions":
            entry["seconds"] = multipart_wav_seconds(request.content)
        else:
            try:
                entry["json"] = json.loads(request.content)
            except ValueError:
                entry["json"] = None
        self.calls.append(entry)
        queue = self.fail_first.get(path)
        if queue:
            status = queue.pop(0)
            return httpx.Response(status, json={"error": {"message": "mock failure", "code": "mock"}}, headers={"retry-after": "0"} if status == 429 else {})
        if path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": self.transcript, "usage": None})
        if path == "/v1/audio/speech":
            if self.stall_speech_over and len(entry["json"]["input"]) > self.stall_speech_over:
                raise httpx.ReadTimeout("stalled")
            return httpx.Response(200, content=wav_bytes(1.0), headers={"content-type": "audio/mpeg"})
        if path == "/v1/chat/completions":
            body = entry["json"]
            if body["model"] == "indic-ocr":
                return httpx.Response(200, json={"choices": [{"message": {"content": self.ocr_text}, "finish_reason": "stop"}],
                                                 "usage": {"completion_tokens": 20}, "blocks": [{"conf": self.ocr_conf, "text": "x"}]})
            target = body.get("target_language_code", "hi")
            text = body["messages"][0]["content"]
            out = self.translate_fn(text, target) if self.translate_fn else self.translate_map.get(target, "x")
            if "[[M0]]" in text:
                out += " [[M0]]"
            return httpx.Response(200, json={"choices": [{"message": {"content": out}, "finish_reason": "stop"}], "usage": {"completion_tokens": 10}})
        return httpx.Response(404, json={"detail": "Not Found"})

    def count(self, path: str) -> int:
        return sum(1 for c in self.calls if c["path"] == path)


@pytest.fixture
def bodhan():
    mock = MockBodhan()
    client.configure_for_tests(httpx.MockTransport(mock.handler))
    return mock


# ---- a real local HTTP server so the MCP server SUBPROCESS can be pointed at a fake Bodhan API ------------
class _FakeHandler(BaseHTTPRequestHandler):
    mock: MockBodhan

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        req = httpx.Request("POST", f"http://x{self.path}", content=body, headers=dict(self.headers))
        resp = self.mock.handler(req)
        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() not in ("content-length", "content-encoding"):
                self.send_header(k, v)
        self.send_header("content-length", str(len(resp.content)))
        self.end_headers()
        self.wfile.write(resp.content)


@pytest.fixture
def fake_bodhan_server():
    mock = MockBodhan()

    class H(_FakeHandler):
        pass

    H.mock = mock
    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", mock
    srv.shutdown()
