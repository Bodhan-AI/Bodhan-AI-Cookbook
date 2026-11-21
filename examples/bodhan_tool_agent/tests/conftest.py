"""Shared fixtures: a fake Bodhan API (httpx MockTransport), WAV generators and a scripted fake LLM."""

from __future__ import annotations

import array
import json
import math
import re
import wave
from pathlib import Path

import httpx
import pytest

from bodhan_core.config import Settings
from bodhan_core.runtime import Runtime

HINDI = "नमस्ते दुनिया यह एक परीक्षण है"


def make_wav(path: Path, seconds: float, rate: int = 16000, silence_at: tuple[float, ...] = (), tone: bool = True) -> Path:
    """Sine wave with short silences at the given times (so window cutting has quiet points to find)."""
    n = int(seconds * rate)
    s = array.array("h", [0] * n)
    if tone:
        for i in range(n):
            s[i] = int(8000 * math.sin(2 * math.pi * (220 + 2 * i / rate) * i / rate))  # drifting pitch: windows differ
        for t in silence_at:
            a = int(t * rate)
            for i in range(a, min(a + int(0.2 * rate), n)):
                s[i] = 0
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(s.tobytes())
    return path


def tts_wav_bytes(seconds: float = 1.0, rate: int = 24000) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(array.array("h", [int(5000 * math.sin(i / 20)) for i in range(int(rate * seconds))]).tobytes())
    return buf.getvalue()


class FakeBodhan:
    """Records every request so tests can assert on call counts (cost / dedupe / chunking)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.stt_text = HINDI
        self.fail_with: dict[str, int] = {}  # path -> status code to return
        self.translate_in_latin = False  # simulate a model answering in the wrong script

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in self.fail_with:
            return httpx.Response(self.fail_with[path], json={"error": {"message": "boom", "code": "x", "request_id": "r"}}, headers={"retry-after": "0"})
        if path == "/v1/audio/transcriptions":
            self.calls.append((path, {"language": re.findall(rb'name="language"\r\n\r\n(\w+)', request.content)}))
            return httpx.Response(200, json={"text": self.stt_text})
        body = json.loads(request.content) if request.content else {}
        self.calls.append((path, body))
        if path == "/translate":
            target = body["target_language"]
            text = body["text"]
            out = f"अनुवाद {len(text)}" if (target != "en" and not self.translate_in_latin) else f"English translation of: {text[:20]}"
            return httpx.Response(200, json={"translation": out})
        if path == "/v1/audio/speech":
            return httpx.Response(200, content=tts_wav_bytes(1.0), headers={"content-type": "audio/wav"})
        if path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "# शीर्षक\n\n| नाम | अंक |\n|---|---|\n| राम | 10 |"}}], "blocks": [{}, {}]})
        return httpx.Response(404, json={"error": {"message": "nope", "code": "nf", "request_id": "r"}})


@pytest.fixture
def fake() -> FakeBodhan:
    return FakeBodhan()


@pytest.fixture
def rt(tmp_path, fake) -> Runtime:
    settings = Settings(keys={m: "test-key" for m in ("indic-transcribe", "indic-translate", "indic-speak", "indic-ocr")}, max_retries=2)
    r = Runtime.create(work_dir=tmp_path / "out", max_cost_inr=20, settings=settings, transport=httpx.MockTransport(fake))
    r.client._sleep = lambda s: None  # no real backoff sleeping in tests
    return r


class ScriptedLLM:
    """Plays back a list of replies; each reply is (text, [(tool_name, args_dict), ...])."""

    model = "scripted"

    def __init__(self, script):
        from bodhan_agent.llm import LLMReply, ToolCall

        self._replies = [LLMReply(content=t, tool_calls=[ToolCall(f"c{i}_{j}", n, json.dumps(a, ensure_ascii=False)) for j, (n, a) in enumerate(calls)]) for i, (t, calls) in enumerate(script)]
        self.seen_messages: list[list[dict]] = []

    def chat(self, messages, tools):
        self.seen_messages.append(json.loads(json.dumps(messages)))
        return self._replies.pop(0) if self._replies else self._replies_done()

    def _replies_done(self):
        from bodhan_agent.llm import LLMReply

        return LLMReply(content="(no more script)")
