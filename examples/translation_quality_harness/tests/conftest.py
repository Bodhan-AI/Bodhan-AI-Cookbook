from __future__ import annotations

import io
import json
from collections.abc import Callable

import httpx
import numpy as np
import pytest
import soundfile as sf

from bodhan_eval.audio.io import Audio
from bodhan_eval.checks.base import ItemContext
from bodhan_eval.languages import get_language
from bodhan_eval.models import Item

HI = "पानी समुद्र तल पर 100 °C पर उबलता है।"
HI_NATIVE_DIGITS = "पानी समुद्र तल पर १०० °C पर उबलता है।"
EN = "Water boils at 100 °C at sea level."


def make_ctx(source: str, translation: str | None, lang: str = "hi", **kw) -> ItemContext:
    item = Item(id=kw.pop("id", "x"), source=source, lang=lang, translation=translation, reference=kw.pop("reference", None))
    ctx = ItemContext(item=item, lang=get_language(lang), uid=item.id, translation=translation)
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def speechlike(seconds: float = 3.0, rate: int = 24000, seed: int = 0, pauses: list[tuple[float, float]] | None = None) -> Audio:
    """Harmonic 'voiced' signal with a 4 Hz syllable envelope: low spectral flatness like speech."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    f0 = 140 + 20 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / rate
    voiced = sum((0.5 / k) * np.sin(k * phase) for k in range(1, 8))
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t) ** 2
    x = 0.3 * voiced * envelope + 0.002 * rng.standard_normal(len(t))
    lead = int(0.2 * rate)
    x[:lead] = 0.0005 * rng.standard_normal(lead)
    x[-lead:] = 0.0005 * rng.standard_normal(lead)
    for s, e in pauses or []:
        x[int(s * rate) : int(e * rate)] = 0.0005 * rng.standard_normal(int(e * rate) - int(s * rate))
    return Audio(x.astype(np.float32), rate)


def wav_bytes(audio: Audio) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio.samples, audio.rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class Router:
    """httpx.MockTransport handler that records calls and dispatches on path."""

    def __init__(self, routes: dict[str, Callable[[httpx.Request], httpx.Response]]):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        for suffix, fn in self.routes.items():
            if request.url.path.endswith(suffix):
                return fn(request)
        return httpx.Response(404, json={"error": {"message": "no route", "code": "not_found"}})


def json_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


@pytest.fixture
def router_factory():
    return Router
