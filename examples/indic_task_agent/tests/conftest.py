"""Shared fixtures. No test calls the real Bodhan API or a real LLM: Bodhan is faked with respx using the
response shapes recorded in probes/FINDINGS.md, and Indian-language sample text comes from recorded
Bodhan outputs (tests/fixtures/bodhan_samples.json)."""

from __future__ import annotations

import io
import json
import math
import shutil
import struct
import wave
from pathlib import Path

import httpx
import pytest
import respx

from bodhan_mcp.client import MODEL_KEY_ENV, Settings
from bodhan_mcp.tools import Services

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = json.loads((FIXTURES / "bodhan_samples.json").read_text(encoding="utf-8"))
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
BASE = "https://api.bodhan.ai"
KEYS = {model: f"test-key-{model}" for model in MODEL_KEY_ENV}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(keys=KEYS, workdir=tmp_path / "work", cache_dir=tmp_path / "cache",
                  retry_initial=0.01, retry_max=0.02)
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def services(tmp_path):
    svc = Services.create(make_settings(tmp_path), http=httpx.AsyncClient())
    yield svc
    svc.cache.close()


def wav_bytes(segments: list[tuple[str, float]], rate: int = 16_000) -> bytes:
    """Synthetic audio: ('tone', seconds) and ('silence', seconds) segments, mono 16-bit."""
    frames = bytearray()
    for kind, seconds in segments:
        n = int(seconds * rate)
        for i in range(n):
            value = int(9000 * math.sin(2 * math.pi * 440 * i / rate)) if kind == "tone" else 0
            frames += struct.pack("<h", value)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def silent_wav(seconds: float, rate: int = 24_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def chat_json(content: str, finish: str = "stop", tokens: int = 10, **extra) -> dict:
    return {"id": "x", "object": "chat.completion", "model": "m",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"completion_tokens": tokens, "prompt_tokens": 5, "total_tokens": tokens + 5}, **extra}


class FakeBodhan:
    """Mimics the live API: chat completions for translate/transliterate/OCR, WAV for speech, JSON for ASR."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.transcript = SAMPLES["ta_translation"]
        self.ocr_text = "# Worksheet\n\n| Subject | Marks |\n|---|---|\n| Science | 10 |"
        self.speech_seconds = lambda text: max(0.5, len(text) / 12)

    def translate(self, text: str, target: str, script: str) -> str:
        if target == "en":
            return SAMPLES["en_from_hi"]
        if "|" in text:
            return SAMPLES["mr_table_markdown"]
        return SAMPLES["ta_translation"] if target == "ta" else SAMPLES["hi_translation"]

    def chat(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(("chat", body))
        model = body["model"]
        if model == "indic-translate":
            text = body["messages"][0]["content"]
            out = self.translate(text, body["target_language_code"], body.get("target_script", "native"))
            return httpx.Response(200, json=chat_json(out, tokens=max(1, len(out) // 3)))
        if model == "indic-transliterate":
            return httpx.Response(200, json=chat_json(SAMPLES["ta_roman"]))
        if model == "indic-ocr":
            blocks = [{"order": 0, "label": "Table", "type": "Table", "bbox_xyxy": [0, 0, 1, 1], "conf": 0.9,
                       "text": self.ocr_text}]
            return httpx.Response(200, json=chat_json(self.ocr_text, blocks=blocks))
        return httpx.Response(404, json={"detail": "Not Found"})

    def speech(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(("speech", body))
        return httpx.Response(200, content=silent_wav(self.speech_seconds(body["input"])),
                              headers={"content-type": "audio/wav"})

    def transcribe(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(("transcribe", {}))
        return httpx.Response(200, json={"text": self.transcript, "usage": None})

    def count(self, kind: str, model: str | None = None) -> int:
        return sum(1 for k, b in self.calls if k == kind and (model is None or b.get("model") == model))


@pytest.fixture
def fake_bodhan():
    fake = FakeBodhan()
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.post("/v1/chat/completions").mock(side_effect=fake.chat)
        router.post("/v1/audio/speech").mock(side_effect=fake.speech)
        router.post("/v1/audio/transcriptions").mock(side_effect=fake.transcribe)
        fake.router = router
        yield fake
