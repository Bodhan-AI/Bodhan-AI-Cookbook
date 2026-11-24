"""Fakes for offline tests: a fake Bodhan API and helpers to build audio.

No test touches the network. The fake API behaves like the real one in shape:
  * /v1/audio/speech returns a WAV whose length grows with the text
  * /v1/audio/transcriptions returns the text that produced a clip, if known
  * /translate returns a marked-up copy of the input in the target script
  * /v1/chat/completions (OCR) returns canned Markdown
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from bodhan_mcp import media
from bodhan_mcp.client import BodhanClient, Ledger
from bodhan_mcp.config import Settings
from bodhan_mcp.tools import ToolContext, Workspace

SAMPLE = {"hi": "नमस्ते", "ta": "வணக்கம்", "bn": "নমস্কার", "te": "నమస్తే", "mr": "नमस्कार", "en": "hello"}


def chat_reply(text: str) -> dict:
    """The shape the live chat route returns for indic-translate."""
    return {"choices": [{"finish_reason": "stop", "index": 0, "message": {"role": "assistant", "content": text}}], "usage": {"completion_tokens": max(1, len(text) // 3)}}


class FakeResponse:
    def __init__(self, status=200, body=None, content=b"", headers=None):
        self.status_code, self._body, self.content, self.headers = status, body, content, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def tone(seconds: float, rate: int = 16000, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * freq * t) * 12000).astype(np.int16)


def silence(seconds: float, rate: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.int16)


class FakeBodhan:
    """Stands in for requests.Session. Records every call so tests can count them."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.heard: dict[str, str] = {}  # sha of a spoken clip -> its text
        self.transcripts: list[str] = []  # queued answers for clips that were not spoken by us
        self.fail_next: list[FakeResponse] = []  # responses to return before behaving normally
        self.speak_glitch: dict[str, str] = {}  # text -> "repeat" | "omit", applied on first synthesis only
        self.ocr_pages: list[str] = ["# Page\n\nSome text."]
        self.translate_break_tables = False

    def post(self, url, headers=None, timeout=None, **kw):
        path = url.split("api.bodhan.ai")[-1]
        self.calls.append((path, kw))
        if self.fail_next:
            return self.fail_next.pop(0)
        if path == "/v1/audio/speech":
            body = kw["json"]
            text = body["input"]
            said = text
            glitch = self.speak_glitch.pop(text, None)
            if glitch == "repeat":
                said = text + " " + text + " " + text
            elif glitch == "omit":
                said = text[: len(text) // 4]
            wav = media.pcm_to_wav(tone(max(0.3, len(said) / 14.0), 24000, 200 + len(self.calls)), 24000)
            self.heard[hashlib.sha256(wav).hexdigest()] = said
            return FakeResponse(content=wav)
        if path == "/v1/audio/transcriptions":
            wav = kw["files"]["file"][1]
            key = hashlib.sha256(wav).hexdigest()
            if key in self.heard:
                return FakeResponse(body={"text": self.heard[key]})
            return FakeResponse(body={"text": self.transcripts.pop(0) if self.transcripts else ""})
        if path == "/v1/chat/completions" and kw["json"].get("model") == "indic-translate":
            body = kw["json"]
            text, lang = body["messages"][0]["content"], body["target_language"]
            if self.translate_break_tables and "|" in text and "\n" in text:
                return FakeResponse(body=chat_reply(SAMPLE.get(lang, "x") + " table lost"))
            # Keep digits, pipes and line breaks; replace each word with a word in the target script.
            word = SAMPLE.get(lang, "x")
            out = "\n".join(" ".join(tok if not any(c.isalpha() for c in tok) else word for tok in line.split(" ")) for line in text.split("\n"))
            return FakeResponse(body=chat_reply(out))
        if path == "/v1/chat/completions":
            ocr_calls = [c for c in self.calls if c[0] == path and c[1]["json"].get("model") == "indic-ocr"]
            page = self.ocr_pages[min(len(ocr_calls) - 1, len(self.ocr_pages) - 1)]
            return FakeResponse(body={"choices": [{"message": {"content": page}}], "blocks": [{"order": 0, "label": "paragraph", "conf": 0.95, "text": page}]})
        return FakeResponse(status=404, body={"error": {"message": "not found", "code": "not_found", "request_id": "r1"}})

    def count(self, path: str) -> int:
        """Calls to one route. "/translate" counts translation calls, which travel on the chat route."""
        if path == "/translate":
            return sum(1 for p, kw in self.calls if p == "/v1/chat/completions" and kw.get("json", {}).get("model") == "indic-translate")
        if path == "/v1/chat/completions":
            return sum(1 for p, kw in self.calls if p == path and kw.get("json", {}).get("model") != "indic-translate")
        return sum(1 for p, _ in self.calls if p == path)


@pytest.fixture
def fake():
    return FakeBodhan()


@pytest.fixture
def ctx(tmp_path, fake, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)  # retries should not slow tests down
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    keys = {name: "test-key" for name in ("transcribe", "translate", "speak", "ocr")}
    settings = Settings(keys=keys, cache_dir=tmp_path / "cache", max_retries=2)
    client = BodhanClient(settings, Ledger(max_cost_inr=50.0), session=fake)
    return ToolContext(client=client, workspace=Workspace([inputs], tmp_path / "out" / "artifacts"))


@pytest.fixture
def inputs(ctx) -> Path:
    return ctx.workspace.input_roots[0]


def write_wav(path: Path, samples: np.ndarray, rate: int = 16000) -> Path:
    path.write_bytes(media.pcm_to_wav(samples, rate))
    return path


def have_ffmpeg() -> bool:
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True, check=False).returncode == 0
    except FileNotFoundError:
        return False


needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is not installed")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
