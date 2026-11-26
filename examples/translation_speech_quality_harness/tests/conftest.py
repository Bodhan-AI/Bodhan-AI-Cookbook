import io
import json
import math
import struct
import wave

import pytest

from bodhan_eval.cache import Cache
from bodhan_eval.clients import Services
from bodhan_eval.cost import CostTracker


def make_wav(seconds=2.0, sr=16000, amp=0.3, silence=None, clip=False) -> bytes:
    n = int(seconds * sr)
    xs = [amp * math.sin(2 * math.pi * 220 * i / sr) for i in range(n)]
    if silence:
        a, b = int(silence[0] * sr), int(silence[1] * sr)
        xs[a:b] = [0.0] * (b - a)
    if clip:
        xs = [1.0 if x > 0 else -1.0 for x in xs]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, x)) * 32767)) for x in xs))
    return buf.getvalue()


class FakeBackend:
    """Deterministic stand-in: 'translation' is a marker transform, back-translation inverts it."""
    m_translate = "fake-t"; m_speak = "fake-s"; m_transcribe = "fake-a"; llm_model = "fake-llm"; voice = "v"

    def __init__(self, judge_errors=None, transcripts=None):
        self.judge_errors = judge_errors or {}
        self.transcripts = transcripts or {}
        self.calls = {"translate": 0, "speak": 0, "transcribe": 0, "chat": 0}

    def llm_configured(self):
        return True

    def translate(self, text, src, tgt):
        self.calls["translate"] += 1
        return text.replace("<hi>", "") if tgt == "en" else text

    def speak(self, text, lang):
        self.calls["speak"] += 1
        return make_wav(2.0)

    def transcribe(self, audio, lang):
        self.calls["transcribe"] += 1
        return self.transcripts.get("default", "")

    def chat(self, messages, temperature):
        self.calls["chat"] += 1
        user = messages[-1]["content"]
        for needle, errs in self.judge_errors.items():
            if needle in user:
                return json.dumps({"errors": errs}), (10, 5)
        return json.dumps({"errors": []}), (10, 5)


@pytest.fixture
def make_services(tmp_path):
    def _mk(backend=None, max_inr=None):
        return Services(backend or FakeBackend(), Cache(tmp_path / "cache"), CostTracker(max_inr), concurrency=2, backoff=0)
    return _mk
