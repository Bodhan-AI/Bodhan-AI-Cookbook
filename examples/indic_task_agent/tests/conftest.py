import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeBodhan  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("BODHAN_MCP_WORKDIR", str(tmp_path / "work"))
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")
    return tmp_path


@pytest.fixture
def fake(env, monkeypatch):
    from bodhan_mcp import bodhan

    f = FakeBodhan()
    monkeypatch.setattr(bodhan, "_post", f.post)
    return f


@pytest.fixture
def speech_wav(tmp_path) -> Path:
    """70 s of 10 s tone / 3 s silence, so the chunker has pauses to cut at."""
    out = tmp_path / "speech.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*300*t)*lt(mod(t,13),10)':s=16000:d=70", str(out)], check=True)
    return out
