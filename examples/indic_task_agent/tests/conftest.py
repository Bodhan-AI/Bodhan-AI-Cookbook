from __future__ import annotations

import io
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeBodhan  # noqa: E402

from bodhan_mcp.client import BodhanClient  # noqa: E402
from bodhan_mcp.config import BodhanKeys  # noqa: E402
from bodhan_mcp.media import ffmpeg_bin  # noqa: E402
from bodhan_mcp.runtime import CostLedger, ResultCache, RunContext  # noqa: E402

KEYS = BodhanKeys("k-transcribe", "k-translate", "k-speak", "k-ocr")


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch):
    """Offline tests must never reach the real API."""
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        if os.environ.get("RUN_LIVE") != "1":
            monkeypatch.delenv(k, raising=False)


@pytest.fixture
def fake() -> FakeBodhan:
    return FakeBodhan()


@pytest.fixture
def make_ctx(tmp_path):
    clients: list[BodhanClient] = []

    def _make(fake: FakeBodhan, max_inr: float | None = None, read_roots=None, events: list | None = None) -> RunContext:
        ledger = CostLedger(max_inr=max_inr)
        cache = ResultCache(None)
        trace = (events.append if events is not None else None)
        client = BodhanClient(KEYS, ledger, cache, trace, transport=fake.transport, backoff_base=0.0, timeout=5)
        clients.append(client)
        out = tmp_path / "artifacts"
        out.mkdir(exist_ok=True)
        return RunContext(artifacts_dir=out, ledger=ledger, cache=cache, client=client, read_roots=read_roots, trace=trace)

    yield _make


def speechlike(seconds: float, rate: int = 16000, pause_every: float = 6.0, seed: int = 1) -> np.ndarray:
    """Bursts of noisy tone ('speech') separated by 0.6 s silences."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    t = np.arange(n) / rate
    x = 0.25 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * rng.random(n))
    period = pause_every + 0.6
    x[(t % period) > pause_every] = 0.0
    return x.astype(np.float32)


def write_wav(path: Path, x: np.ndarray, rate: int = 16000) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    path.write_bytes(buf.getvalue())
    return path


def ffmpeg(*args: str) -> None:
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def make_pdf(path: Path, pages: int) -> Path:
    from PIL import Image, ImageDraw

    imgs = []
    for i in range(pages):
        im = Image.new("RGB", (600, 800), "white")
        ImageDraw.Draw(im).text((40, 40), f"page {i + 1}", fill="black")
        imgs.append(im)
    imgs[0].save(path, "PDF", save_all=True, append_images=imgs[1:])
    return path


def make_image(path: Path, size=(800, 600)) -> Path:
    from PIL import Image

    Image.new("RGB", size, "white").save(path)
    return path
