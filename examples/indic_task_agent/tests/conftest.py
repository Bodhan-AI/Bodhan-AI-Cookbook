import io
import shutil
import sys
import wave
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bodhan_mcp.bodhan import BodhanClient, Budget  # noqa: E402
from bodhan_mcp.tools import Context  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")


def wav_bytes(seconds: float, rate: int = 24000) -> bytes:
    """Silent PCM16 mono WAV of the given length, like Indic-Speak output."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def make_client(tmp_path: Path, handler, *, budget: Budget | None = None, max_attempts: int = 4, env: dict | None = None) -> BodhanClient:
    env = {"BODHAN_API_KEY": "test-key", **(env or {})}
    return BodhanClient(
        cache_dir=tmp_path / "cache",
        budget=budget,
        env=env,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        max_attempts=max_attempts,
    )


@pytest.fixture
def ctx(tmp_path):
    client = make_client(tmp_path, lambda req: httpx.Response(500, json={"error": {"code": "internal_error", "message": "no handler"}}))
    return Context(client=client, work_dir=tmp_path / "work")
