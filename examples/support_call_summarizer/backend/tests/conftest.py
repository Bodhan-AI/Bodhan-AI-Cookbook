"""Shared fixtures: synthetic call audio and fake Bodhan and Sarvam clients.

No test here needs an API key or internet access.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from app.bodhan_client import BodhanError, Translation
from app.config import Settings
from app.llm import ChatResult

SR = 16_000
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def synth(pattern: list[tuple[str, float]], seed: int = 0) -> np.ndarray:
    """Build int16 audio from ("speech" | "silence" | "dip", seconds) parts.

    "speech" is a voice-like buzz with a syllable-rate wobble, "silence" is faint
    noise like a quiet phone line, and "dip" is a short quiet moment inside speech.
    """
    rng = np.random.default_rng(seed)
    parts = []
    for kind, seconds in pattern:
        n = int(seconds * SR)
        t = np.arange(n) / SR
        if kind == "speech":
            voice = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((140, 280, 420, 700), start=1))
            wobble = 0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 3.5 * t))
            parts.append(6000 * voice * wobble / 2 + rng.normal(0, 30, n))
        else:
            parts.append(rng.normal(0, 30 if kind == "silence" else 60, n))
    return np.clip(np.concatenate(parts), -32768, 32767).astype(np.int16)


def write_wav(path: Path, samples: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(samples.tobytes())
    return path


def encode(src: Path, dst: Path) -> Path:
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), str(dst)], check=True)
    return dst


# A short two-sided conversation: 4 utterances separated by pauses.
CONVERSATION = [
    ("silence", 0.5),
    ("speech", 4.0),
    ("silence", 1.5),
    ("speech", 6.0),
    ("silence", 1.6),
    ("speech", 3.0),
    ("silence", 2.0),
    ("speech", 5.0),
    ("silence", 0.8),
]


@pytest.fixture
def call_wav(tmp_path: Path) -> Path:
    return write_wav(tmp_path / "call.wav", synth(CONVERSATION))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        bodhan_api_key="test-key-placeholder",
        web_dist_dir=tmp_path / "no-web-build",
        max_file_mb=5,
        max_duration_seconds=120,
        diarization_provider="none",
    )


class FakeBodhan:
    """Stands in for ``BodhanClient``. Behaviour is set per test via callables."""

    def __init__(
        self,
        transcribe: Callable[[int], str] | None = None,
        translate: Callable[[str], Translation] | None = None,
    ):
        self._transcribe = transcribe or (lambda n: f"नमस्ते {n}")
        self._translate = translate or (lambda text: Translation(text=f"EN({text})", truncated=False))
        self.transcribe_calls: list[str | None] = []
        self.translate_calls: list[str] = []

    async def transcribe(self, wav: bytes, language: str | None = None) -> str:
        assert wav[:4] == b"RIFF", "pieces must be sent as WAV"
        self.transcribe_calls.append(language)
        n = len(self.transcribe_calls)
        result = self._transcribe(n)
        if isinstance(result, Exception):
            raise result
        return result

    async def translate(self, text: str, target_language: str = "en") -> Translation:
        assert target_language == "en"
        self.translate_calls.append(text)
        result = self._translate(text)
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self) -> None:
        pass


def bodhan_error(status: int, code: str) -> BodhanError:
    return BodhanError(status, code, code.replace("_", " "), "req-test")


def analysis_answer(**overrides) -> dict:
    """A valid answer from the Sarvam model, as ``analysis.ANSWER_SCHEMA`` asks for."""
    return {
        "summary": "The customer's internet was down; the agent reset the router and it works again.",
        "customer_issue": "Internet not working for two days.",
        "resolution": "resolved",
        "action_items": ["Agent to call back tomorrow"],
        "topics": ["internet outage", "router"],
        "overall_sentiment": "mixed",
        "sentiment_score": 0.2,
        "customer_sentiment_trend": "improved",
        "sentiment_explanation": "Angry at first, thankful at the end.",
        "segment_sentiments": [{"segment_no": 1, "sentiment": "negative"}, {"segment_no": 4, "sentiment": "positive"}],
        **overrides,
    }


class FakeSarvam:
    """Stands in for ``SarvamClient``: ``reply`` returns a ChatResult, a dict (sent as JSON) or an exception."""

    provider = "Sarvam"
    model = "sarvam-105b"

    def __init__(self, reply: Callable[[list[dict]], object] | None = None):
        self._reply = reply or (lambda _messages: analysis_answer())
        self.calls: list[list[dict]] = []

    async def chat(self, messages, *, response_format=None) -> ChatResult:
        self.calls.append(messages)
        result = self._reply(messages)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, dict):
            return ChatResult(json.dumps(result), truncated=False)
        return result

    async def aclose(self) -> None:
        pass


class FakeClaude(FakeSarvam):
    """Stands in for ``ClaudeClient``, the fallback."""

    provider = "Claude"
    model = "claude-opus-5-5"
