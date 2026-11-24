"""Offline test doubles for the Bodhan and LLM clients (no network, no keys)."""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf

from bodhan_eval.clients import Budget


def tone(seconds: float, sr: int = 24000, freq: float = 220.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    # Harmonic-rich tone so it is not spectrally flat (reads as "speech", not noise).
    return (amp * sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 6))).astype("float32")


def to_wav(x: np.ndarray, sr: int = 24000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class FakeBodhan:
    """translate(): back-translations come from a lookup table; transcribe(): from a table keyed by audio length."""

    def __init__(self, back: dict[str, str] | None = None, transcripts: dict[int, str] | None = None):
        self.back = back or {}
        self.transcripts = transcripts or {}
        self.budget = Budget(None)
        self.calls = 0

    def translate(self, text: str, target: str) -> dict:
        self.calls += 1
        out = self.back.get(text, text) if target == "en" else f"[{target}] {text}"
        return {"translation": out, "truncated": False, "tokens": len(out)}

    def speak(self, text: str, lang: str, voice=None):
        self.calls += 1
        return to_wav(tone(0.06 * len(text) + 0.3)), len(text) * 6 / 10_000

    def transcribe(self, wav: bytes, lang: str) -> dict:
        self.calls += 1
        return {"text": self.transcripts.get(len(wav), ""), "cost": 0.0}


class FakeLLM:
    model = "fake-judge"

    def __init__(self, answers: dict[str, dict] | None = None):
        self.answers = answers or {}

    def json(self, system: str, user: str, *, salt: str = "") -> dict:
        for needle, answer in self.answers.items():
            if needle in user:
                return answer
        return {"errors": []}


@pytest.fixture
def fake_bodhan():
    return FakeBodhan()
