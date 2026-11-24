from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SR = 16_000


def burst(duration_s: float, *, amplitude: float = 0.25, frequency: float = 220.0) -> np.ndarray:
    t = np.arange(int(duration_s * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def silence(duration_s: float) -> np.ndarray:
    return np.zeros(int(duration_s * SR), dtype=np.float32)


def build_signal(segments: list[tuple[float, float]], total_s: float, *, noise: float = 1e-3) -> np.ndarray:
    """A timeline with a tone in each ``(start, end)`` and low noise elsewhere."""
    samples = (noise * np.random.default_rng(0).standard_normal(int(total_s * SR))).astype(np.float32)
    for start, end in segments:
        first = int(start * SR)
        last = int(end * SR)
        samples[first:last] = burst((last - first) / SR)
    return samples


@pytest.fixture
def sr() -> int:
    return SR
