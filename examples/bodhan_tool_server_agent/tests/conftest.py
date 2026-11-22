import io
import math
import struct
import wave

import pytest


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_WORKDIR", str(tmp_path / "work"))
    monkeypatch.setenv("BODHAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("BODHAN_INPUT_DIRS", str(tmp_path))
    for k in ("TRANSCRIBE", "TRANSLATE", "SPEAK", "OCR"):
        monkeypatch.setenv(f"BODHAN_{k}_KEY", "test-key")
    monkeypatch.delenv("BODHAN_MAX_COST_INR", raising=False)
    monkeypatch.setattr("bodhan_mcp.core.asyncio.sleep", _nosleep)
    return tmp_path


async def _nosleep(_):
    return None


def make_wav(path, seconds, rate=24000, tone=True):
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20)) if tone else 0) for i in range(n)))
    return path


def wav_bytes(seconds, rate=24000):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return b.getvalue()
