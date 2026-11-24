"""Offline fakes: a fake Bodhan API (httpx MockTransport) and generated media.

The fake is deterministic so tests can assert on chunking, verification, retries and caching
without network access or API keys.
"""

from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw

from bodhan_mcp import config
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.tools import Toolkit

SR = 16000
SCRIPT_BASE = {"hi": 0x0915, "mr": 0x0915, "ta": 0x0B95, "bn": 0x0995, "te": 0x0C15, "kn": 0x0C95, "gu": 0x0A95}
SPOKEN = {440: ("hi", "नमस्ते यह एक परीक्षण संदेश है"), 660: ("te", "నమస్కారం ఇది పరీక్ష"),
          880: ("en", "hello this is a test message")}


def fake_translate(text: str, target: str) -> str:
    if target == "en":
        return "".join("x" if (ord(c) > 0x900 and c.isalpha()) else c for c in text)
    base = SCRIPT_BASE.get(target, 0x0915)
    return "".join(chr(base + (ord(c.lower()) - 97) % 26) if c.isascii() and c.isalpha() else c for c in text)


class FakeBodhan:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.tts_text: dict[str, str] = {}
        self.corrupt_tts_once: set[str] = set()   # texts whose first synthesis "repeats" content
        self.fail_next: list[int] = []             # status codes to return before succeeding
        self.translate_route_404 = False           # simulate a gateway that only serves the chat transport

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={"error": {"code": "x", "message": "fake", "request_id": "r"}})
        path = request.url.path
        if path == "/v1/audio/transcriptions":
            return self._transcribe(request)
        body = json.loads(request.content or b"{}")
        self.calls.append((path, body))
        if path == "/translate" and self.translate_route_404:
            return httpx.Response(404, json={"detail": "Not Found"})
        if path == "/v1/chat/completions" and body.get("model") == "indic-translate":
            text = body["messages"][0]["content"]
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": fake_translate(text, body["target_language"])}}], "usage": {"completion_tokens": 5}})
        if path == "/translate":
            return httpx.Response(200, json={"translation": fake_translate(body["text"], body["target_language"]),
                                             "target_language": body["target_language"], "truncated": False,
                                             "usage": {"output_tokens": len(body["text"]) // 3}})
        if path == "/v1/audio/speech":
            text = body["input"]
            secs = max(0.4, len(text) * 0.06)
            t = np.arange(int(24000 * secs)) / 24000
            pcm = (np.sin(2 * np.pi * 1000 * t) * 8000).astype(np.int16)
            pcm[: 10 + len(self.tts_text) % 50] = 0  # make each clip unique
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(pcm.tobytes())
            wav = buf.getvalue()
            said = text
            if text in self.corrupt_tts_once:
                self.corrupt_tts_once.discard(text)
                said = text + " " + text + " " + text
            self.tts_text[hashlib.sha256(wav).hexdigest()] = said
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        if path == "/v1/chat/completions":
            n = sum(1 for p, _ in self.calls if p == path)
            md = f"## Page heading {n}\n\nThe angle is 90 degrees and the line has 2 points.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
            return httpx.Response(200, json={"choices": [{"message": {"content": md}}], "blocks": [
                {"order": 1, "label": "title", "bbox_xyxy": [0, 0, 1, 1], "conf": 0.95, "text": "Page heading"},
                {"order": 2, "label": "paragraph", "bbox_xyxy": [0, 0, 1, 1], "conf": 0.42, "text": "smudged"}]})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})

    def _transcribe(self, request: httpx.Request) -> httpx.Response:
        raw = request.content
        start = raw.find(b"RIFF")
        wav = raw[start:]
        boundary = request.headers["content-type"].split("boundary=")[1].encode()
        wav = wav[: wav.find(b"\r\n--" + boundary)]
        lang = None
        for part in raw.split(b"--" + boundary):
            if b'name="language"' in part:
                lang = part.split(b"\r\n\r\n", 1)[1].strip().decode()
        self.calls.append(("/v1/audio/transcriptions", {"language": lang, "bytes": len(wav)}))
        h = hashlib.sha256(wav).hexdigest()
        if h in self.tts_text:
            return httpx.Response(200, json={"text": self.tts_text[h]})
        with wave.open(io.BytesIO(wav), "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
            rate = wf.getframerate()
        spec = np.abs(np.fft.rfft(pcm))
        freq = np.fft.rfftfreq(len(pcm), 1 / rate)[int(np.argmax(spec))]
        tone = min(SPOKEN, key=lambda f: abs(f - freq))
        return httpx.Response(200, json={"text": SPOKEN[tone][1]})

    def count(self, path: str) -> int:
        return sum(1 for p, _ in self.calls if p == path)


@pytest.fixture
def fake():
    return FakeBodhan()


@pytest.fixture
def kit(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "test-key-placeholder")
    s = config.Settings(cache_dir=tmp_path / "cache", work_dir=tmp_path / "art", max_parallel=2)
    s.max_retries = 2
    client = BodhanClient(s, transport=httpx.MockTransport(fake))
    return Toolkit(client, tmp_path / "art", strict_provenance=True)


def make_tone_wav(path: Path, pattern: list[tuple[float, float]], freq: int = 440) -> Path:
    """pattern: [(seconds, amplitude)] - amplitude 0 means silence."""
    parts = []
    for secs, amp in pattern:
        t = np.arange(int(SR * secs)) / SR
        parts.append((np.sin(2 * np.pi * freq * t) * amp).astype(np.int16))
    pcm = np.concatenate(parts)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return path


@pytest.fixture
def media_dir(tmp_path):
    d = tmp_path / "inputs"
    d.mkdir()
    # 70 s "voice note": speech bursts separated by short pauses
    make_tone_wav(d / "voice_note.wav", [(9, 6000), (0.6, 0)] * 7, 440)
    make_tone_wav(d / "clip_te.wav", [(4, 6000)], 660)
    make_tone_wav(d / "clip_hi.wav", [(4, 6000)], 440)
    make_tone_wav(d / "silence.wav", [(5, 0)])
    pages = []
    for i in range(3):
        img = Image.new("RGB", (600, 800), "white")
        ImageDraw.Draw(img).text((40, 40), f"Chapter page {i + 1}", fill="black")
        pages.append(img)
    pages[0].save(d / "chapter.pdf", save_all=True, append_images=pages[1:])
    pages[0].save(d / "worksheet.jpg")
    (d / "paragraph.txt").write_text("Plants make food from sunlight. They need water and 6 hours of light.\n")
    return d
