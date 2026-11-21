"""Shared fixtures: a fake Bodhan API (respx), synthetic audio, and a scripted LLM.

The fake API is deterministic and offline:
- translate replaces every word with a word in the target script, keeping digits, pipes and punctuation, so number,
  table-shape and script checks behave as they would on real output;
- speech returns a tone whose length encodes which text it spoke, and transcription of that tone returns the text,
  so back-transcription verification can pass or fail on purpose.
"""

from __future__ import annotations

import io
import json
import math
import re
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx

from bodhan_mcp.cache import DiskCache
from bodhan_mcp.config import Settings
from bodhan_mcp.ledger import Ledger
from bodhan_mcp.tools import ToolContext

BASE = "https://api.bodhan.test"
SCRIPT_WORD = {"ta": "தமிழ்", "hi": "हिंदी", "bn": "বাংলা", "mr": "मराठी", "te": "తెలుగు", "en": "word", "gu": "ગુજરાતી"}


def tone(seconds: float, sr: int = 16000, freq: float = 220.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * 32767 * np.sin(2 * math.pi * freq * t)).astype(np.int16)


def silence(seconds: float, sr: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.int16)


def to_wav(samples: np.ndarray, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


class FakeBodhan:
    def __init__(self):
        self.spoken: list[tuple[float, str]] = []
        self.calls: dict[str, int] = {"transcribe": 0, "translate": 0, "speak": 0, "ocr": 0}
        self.default_transcript = "नमस्ते, यह एक परीक्षण है। आज 25 छात्र आए।"
        self.speak_mode = "good"  # "good" or "repeat": repeat makes back-transcripts loop the text
        self.ocr_text = "# Lines and Angles\n\nTwo lines meet at 90 degrees.\n\n| Angle | Degrees |\n| --- | --- |\n| Right | 90 |"
        self.ocr_fail_once = False
        self.rate_limit_once = False

    def translate(self, request: httpx.Request) -> httpx.Response:
        self.calls["translate"] += 1
        body = json.loads(request.content)
        word = SCRIPT_WORD.get(body["target_language"], "xx")
        out = re.sub(r"[^\W\d_]+", word, body["text"])
        return httpx.Response(200, json={"translation": out, "target_language": body["target_language"], "script": "native",
                                         "truncated": False, "usage": {"output_tokens": max(1, len(out) // 3)}})

    def speak(self, request: httpx.Request) -> httpx.Response:
        self.calls["speak"] += 1
        body = json.loads(request.content)
        text = body["input"]
        n = len(self.spoken)
        seconds = 0.5 + 0.02 * len(text) + 0.0137 * (n % 50)
        self.spoken.append((seconds, text))
        return httpx.Response(200, content=to_wav(tone(seconds, 24000), 24000), headers={"content-type": "audio/wav"})

    def transcribe(self, request: httpx.Request) -> httpx.Response:
        self.calls["transcribe"] += 1
        if self.rate_limit_once:
            self.rate_limit_once = False
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"code": "rate_limited", "message": "slow down"}})
        raw = request.content
        m = re.search(rb"RIFF.*", raw, re.S)
        try:
            seconds = wav_seconds(m.group(0)) if m else 0
        except Exception:
            seconds = 0
        if self.spoken:
            best = min(self.spoken, key=lambda st: abs(st[0] - seconds))
            if abs(best[0] - seconds) < 0.01:
                text = best[1]
                return httpx.Response(200, json={"text": text + " " + text if self.speak_mode == "repeat" else text})
        return httpx.Response(200, json={"text": self.default_transcript})

    def ocr(self, request: httpx.Request) -> httpx.Response:
        self.calls["ocr"] += 1
        if self.ocr_fail_once:
            self.ocr_fail_once = False
            return httpx.Response(502, json={"error": {"code": "block_too_long", "message": "block exceeds max_tokens"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": self.ocr_text}}],
                                         "blocks": [{"order": 0, "label": "title", "conf": 0.97, "text": "Lines and Angles"},
                                                    {"order": 1, "label": "text", "conf": 0.31, "text": "Two lines"}]})


@pytest.fixture
def fake_api():
    fake = FakeBodhan()
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.post("/translate").mock(side_effect=fake.translate)
        router.post("/v1/audio/speech").mock(side_effect=fake.speak)
        router.post("/v1/audio/transcriptions").mock(side_effect=fake.transcribe)
        router.post("/v1/chat/completions").mock(side_effect=fake.ocr)
        yield fake


@pytest.fixture
def env_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key-not-real")
    monkeypatch.setenv("BODHAN_BASE_URL", BASE)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def ctx(workdir, env_keys, fake_api):
    settings = Settings(base_url=BASE, cache_dir=workdir / ".cache", output_dir=workdir / "out", allowed_dirs=[workdir])
    settings.max_retries = 2
    return ToolContext(settings, Ledger(None))


def make_ctx(workdir: Path, max_cost: float | None = None, cache: bool = True) -> ToolContext:
    settings = Settings(base_url=BASE, cache_dir=workdir / ".cache", output_dir=workdir / "out", allowed_dirs=[workdir])
    ledger = Ledger(max_cost)
    from bodhan_mcp.client import BodhanClient

    return ToolContext(settings, ledger, BodhanClient(settings, ledger, DiskCache(settings.cache_dir, enabled=cache)))


class ScriptedLLM:
    """Plays a script: each entry is a function(messages, tools) -> LLMReply. The judge always approves."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen: list[list[dict]] = []

    async def chat(self, messages, tools=None, temperature=0.1):
        from bodhan_agent.llm import LLMReply

        self.calls += 1
        self.seen.append(messages)
        if messages[0]["content"].startswith("You check whether an agent completed a task"):
            return LLMReply(content='{"satisfied": true, "problems": []}')
        if not self.script:
            raise AssertionError("ScriptedLLM ran out of script")
        return self.script.pop(0)(messages, tools)

    def usage(self):
        return {"model": "scripted", "calls": self.calls}


def last_tool_json(messages) -> dict:
    """The JSON inside the most recent tool result message."""
    for m in reversed(messages):
        if m["role"] == "tool":
            text = m["content"]
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start: end + 1])
    raise AssertionError("no tool message")
