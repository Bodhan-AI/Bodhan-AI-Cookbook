"""The demo download scripts read real data formats correctly."""

import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))
import fetch_real_audio  # noqa: E402


def test_reads_float32_wav_like_fleurs():
    x = (np.sin(np.linspace(0, 40, 800)) * 0.5).astype("<f4")
    fmt = struct.pack("<HHIIHH", 3, 1, 16000, 64000, 4, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", x.nbytes) + x.tobytes()
    pcm, rate = fetch_real_audio.read_wav(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    assert rate == 16000
    assert np.abs(np.frombuffer(pcm, "<i2") / 32767 - x).max() < 1e-3
