"""Live contract test against the real Bodhan endpoint.

Skipped unless both a key and ``sample_data/smoke.wav`` are present. It sends a
few seconds of audio once, which costs a fraction of a paisa, and asserts only
the contract the pipeline depends on: a non-empty ``text`` field.

    cp your_clip.wav sample_data/smoke.wav
    export BODHAN_TRANSCRIBE_KEY=...
    pytest tests/test_e2e_live.py -v
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from bodhan_dub import media
from bodhan_dub.cache import Cache
from bodhan_dub.client import BodhanClient
from bodhan_dub.config import load_settings
from bodhan_dub.cost import CostMeter

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data" / "smoke.wav"
HAS_KEY = bool(os.environ.get("BODHAN_TRANSCRIBE_KEY") or os.environ.get("BODHAN_API_KEY"))

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not HAS_KEY, reason="no Bodhan transcription key in the environment"),
    pytest.mark.skipif(not SAMPLE.exists(), reason="sample_data/smoke.wav not present"),
]


def test_live_transcription_returns_text(tmp_path) -> None:
    settings = load_settings()
    client = BodhanClient(
        settings,
        logging.getLogger("live"),
        Cache(tmp_path / "cache"),
        CostMeter(),
    )

    samples, rate = media.read_wav(SAMPLE)
    window = samples[: rate * 5]
    result = client.transcribe(
        media.to_wav_bytes(window, rate), language="en", audio_seconds=len(window) / rate
    )

    assert isinstance(result.data.get("text"), str)
    assert result.data["text"].strip()
    assert result.attempts >= 1
