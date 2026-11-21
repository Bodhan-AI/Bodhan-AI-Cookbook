import hashlib
import io
import json
import wave

import httpx
import pytest

from bodhan_core.gateway import Gateway
from bodhan_core.runtime import Limits, Run
from bodhan_core.tools import Tools


@pytest.fixture
def run(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    return Run(inputs, tmp_path / "out", Limits())


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    for key in (
        "BODHAN_TRANSCRIBE_KEY",
        "BODHAN_TRANSLATE_KEY",
        "BODHAN_SPEAK_KEY",
        "BODHAN_OCR_KEY",
        "LLM_API_KEY",
    ):
        monkeypatch.setenv(key, "mock-only-not-a-real-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://mock-planner.example/v1")
    monkeypatch.setenv("LLM_MODEL", "mock-planner")
    monkeypatch.setenv("LLM_INPUT_INR_PER_MILLION", "10")
    monkeypatch.setenv("LLM_OUTPUT_INR_PER_MILLION", "20")
    # Real .env settings may be loaded by entry-point imports during collection.
    # Keep mocked tests independent; individual configuration tests override these.
    monkeypatch.setenv("LLM_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("LLM_TOKEN_LIMIT_FIELD", "max_tokens")
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setenv("BODHAN_CACHE_ONLY", "0")
    for name, value in {
        "BODHAN_MAX_COST_INR": "20",
        "BODHAN_MAX_STEPS": "30",
        "BODHAN_CALL_TIMEOUT": "45",
        "BODHAN_RUN_TIMEOUT": "300",
    }.items():
        monkeypatch.setenv(name, value)


def wav_bytes(seconds=1, rate=24000):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\x00\x00" * round(seconds * rate))
    return stream.getvalue()


class FakeBodhan:
    """Deterministic transport fixtures. Not models and never used in production code."""

    def __init__(self):
        self.requests = []
        self.spoken = "The class starts at nine."
        self.ocr_text = "The class starts at nine."
        self.translation = None
        self.truncated = False
        self.truncation_limit = None
        self.planner = None
        self.audio_text = {}

    def speech_audio(self, text):
        sample = hashlib.sha256(text.encode()).digest()[:2]
        frames = sample * 16000
        stream = io.BytesIO()
        with wave.open(stream, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(frames)
        self.audio_text[hashlib.sha256(frames).hexdigest()] = text
        return stream.getvalue()

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "mock-planner.example":
            return httpx.Response(200, json=self.planner(request))
        if (
            path == "/v1/chat/completions"
            and json.loads(request.content).get("model") == "indic-translate"
        ):
            body = json.loads(request.content)
            native = {
                "text": body["messages"][0]["content"],
                "target_language": body["target_language_code"],
            }
            text = native["text"] if self.translation is None else self.translation(native)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length"
                            if self.truncated or (
                                self.truncation_limit is not None
                                and len(native["text"].encode()) > self.truncation_limit
                            )
                            else "stop",
                            "message": {"content": text},
                        }
                    ],
                    "usage": {"completion_tokens": 10},
                },
            )
        if path == "/translate":
            body = json.loads(request.content)
            text = body["text"] if self.translation is None else self.translation(body)
            return httpx.Response(
                200,
                json={
                    "translation": text,
                    "target_language": body["target_language"],
                    "truncated": self.truncated,
                    "usage": {"completion_tokens": 10},
                },
            )
        if path == "/v1/audio/transcriptions":
            data = request.content
            start = data.find(b"RIFF")
            text = self.spoken
            if start >= 0:
                with wave.open(io.BytesIO(data[start:]), "rb") as audio:
                    frames = audio.readframes(audio.getnframes())
                    text = self.audio_text.get(hashlib.sha256(frames).hexdigest(), text)
            return httpx.Response(200, json={"text": text})
        if path == "/v1/audio/speech":
            self.spoken = json.loads(request.content)["input"]
            return httpx.Response(
                200, content=self.speech_audio(self.spoken), headers={"content-type": "audio/wav"}
            )
        if path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop", "message": {"content": self.ocr_text}}],
                    "blocks": [],
                },
            )
        raise AssertionError(f"Unexpected URL: {request.url}")


@pytest.fixture
def fake():
    return FakeBodhan()


@pytest.fixture
def tools(run, fake):
    return Tools(run, Gateway(run, httpx.MockTransport(fake)))


def input_ref(run, text="The class starts at nine.", name="source.txt"):
    path = run.input_root / name
    path.write_text(text, encoding="utf-8")
    return run.register(path, "input")
