"""Tests use *only* in-process stand-ins. They do not call paid/live models."""
import io
import wave

import pytest

from bodhan_shared.gateway import Gateway


def fake_wav(seconds=0.8, sample_rate=16000):
    buff = io.BytesIO()
    with wave.open(buff, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\0\0" * int(sample_rate * seconds))
    return buff.getvalue()


@pytest.fixture
def fake_gateway(monkeypatch, tmp_path):
    """Expose actual Gateway (budget/cache) but simulate HTTP responses only."""
    calls = []

    def post(self: Gateway, capability, endpoint, *, payload=None, file=None,
             data=None, is_audio=False):
        calls.append((capability, endpoint, payload, data))
        if capability == "translate":
            return {"translation": payload["text"]}  # deterministic identity response
        if capability == "ocr":
            return {"choices": [{"message": {"content": "# Chapter\n\nA triangle has 3 sides."}}],
                    "blocks": [{"order": 0, "label": "heading", "conf": 0.95,
                                "bbox_xyxy": [0, 0, 10, 10], "text": "Chapter"}]}
        if capability == "transcribe":
            return {"text": "Hello. This audio was transcribed by a simulated Bodhan response.",
                    "language": data.get("language") if data else "en"}
        if capability == "speak":
            return fake_wav()
        raise AssertionError(capability)

    monkeypatch.setattr(Gateway, "_post", post)
    return calls
