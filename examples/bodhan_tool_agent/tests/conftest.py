"""Shared fixtures: a fake Bodhan API (respx) wired into the server's client, and media builders."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from array import array

import httpx
import pytest
import respx
from PIL import Image, ImageDraw

from bodhan_core import audio
from bodhan_core.cache import Cache
from bodhan_core.client import BodhanClient
from bodhan_core.config import Settings
from bodhan_core.ledger import Ledger
from bodhan_mcp import runtime

BASE = "https://api.bodhan.ai"
KEYS = {m: f"test-{m}" for m in ("indic-transcribe", "indic-translate", "indic-speak", "indic-ocr")}


def fake_translate(text: str, target: str) -> str:
    """Deterministic stand-in: letters become target-script letters; digits, spaces and punctuation stay."""
    letter = {"en": "a", "ta": "க", "te": "క", "bn": "ক", "mr": "क", "hi": "क"}.get(target, "क")
    return re.sub(r"[^\W\d_]", letter, text)


class FakeBodhan:
    """Records calls and answers like the real API (shapes taken from docs/api_facts.md)."""

    def __init__(self):
        self.calls: dict[str, int] = {"transcribe": 0, "translate": 0, "speak": 0, "ocr": 0}
        self.spoken: dict[str, str] = {}  # sha of WAV -> text that produced it
        self.transcripts: list[str] = []  # queued transcripts for non-TTS audio, consumed in order
        self.mishear_first_take: set[str] = set()  # texts whose first TTS take is "misheard"
        self.ocr_pages: list[str] = []
        self.fail_ocr_calls: set[int] = set()

    def router(self) -> respx.MockRouter:
        r = respx.mock(base_url=BASE, assert_all_called=False)
        r.post("/v1/audio/transcriptions").mock(side_effect=self.transcribe)
        r.post("/v1/audio/speech").mock(side_effect=self.speak)
        r.post("/v1/chat/completions").mock(side_effect=self.chat)
        return r

    def transcribe(self, request: httpx.Request) -> httpx.Response:
        self.calls["transcribe"] += 1
        body = request.content
        wav = body[body.index(b"RIFF"):]
        digest = hashlib.sha256(wav.split(b"\r\n--")[0]).hexdigest()
        if digest in self.spoken:
            text = self.spoken[digest]
            if text in self.mishear_first_take:
                self.mishear_first_take.discard(text)
                text = "गलत " * 3
            return httpx.Response(200, json={"text": text, "usage": None})
        text = self.transcripts.pop(0) if self.transcripts else f"वाक्य {self.calls['transcribe']}"
        return httpx.Response(200, json={"text": text, "usage": None})

    def speak(self, request: httpx.Request) -> httpx.Response:
        self.calls["speak"] += 1
        body = json.loads(request.content)
        seconds = max(0.4, len(body["input"]) * 0.06)
        n = int(seconds * 24_000)
        samples = array("h", (int(6000 * math.sin(2 * math.pi * 220 * t / 24_000)) for t in range(n)))
        # vary bytes per text so each piece hashes differently
        samples[0] = len(body["input"]) % 1000 + self.calls["speak"]
        wav = audio.wav_bytes(samples, 24_000)
        self.spoken[hashlib.sha256(wav).hexdigest()] = body["input"]
        return httpx.Response(200, content=wav, headers={"content-type": "audio/mpeg"})  # mislabelled, like the real API

    def chat(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] == "indic-ocr":
            self.calls["ocr"] += 1
            if self.calls["ocr"] in self.fail_ocr_calls:
                return httpx.Response(400, json={"error": {"message": "bad image", "code": "400"}})
            text = self.ocr_pages.pop(0) if self.ocr_pages else f"पृष्ठ {self.calls['ocr']}"
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "blocks": [],
                                             "usage": {"completion_tokens": 10}})
        self.calls["translate"] += 1
        text = body["messages"][0]["content"]
        if body["target_language"] not in {"hi", "ta", "te", "bn", "mr", "en", "kn", "ml", "gu", "pa", "or"}:
            return httpx.Response(400, json={"error": {"message": f"unsupported language '{body['target_language']}'",
                                                       "code": "400"}})
        out = fake_translate(text, body["target_language"])
        return httpx.Response(200, json={"choices": [{"message": {"content": out}, "finish_reason": "stop"}],
                                         "usage": {"completion_tokens": len(out) // 2}})


@pytest.fixture(autouse=True)
def _never_read_dotenv(request, monkeypatch):
    """Tests must never pick up real keys from .env (live tests opt out explicitly)."""
    if "live" not in request.keywords:
        monkeypatch.setenv("BODHAN_NO_DOTENV", "1")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Install a fake Bodhan API and a fresh client; outputs go under tmp_path/ws."""
    monkeypatch.setenv("BODHAN_WORKSPACE", str(tmp_path / "ws"))
    fb = FakeBodhan()

    async def no_sleep(_):
        return None

    with fb.router() as router:
        settings = Settings(bodhan_keys=KEYS, max_retries=1, call_timeout_s=5)
        client = BodhanClient(settings, ledger=Ledger(), cache=Cache(tmp_path / "cache.sqlite"), sleep=no_sleep)
        runtime.set_client(client)
        fb.router_obj = router
        yield fb
        runtime.set_client(None)


# --------------------------------------------------------------------------- media builders


def tone_wav(path, pattern, rate=16_000):
    out = array("h")
    for i, (kind, secs) in enumerate(pattern):
        n = int(secs * rate)
        if kind == "tone":
            out.extend(int(7000 * math.sin(2 * math.pi * (200 + 30 * (i % 4)) * t / rate)) for t in range(n))
        else:
            out.extend([0] * n)
    path.write_bytes(audio.wav_bytes(out, rate))
    return path


def page_image(text: str = "page") -> Image.Image:
    img = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(img).text((20, 80), text, fill="black")
    return img


def png_file(path, text="page"):
    page_image(text).save(path, "PNG")
    return path


def pdf_file(path, pages=3):
    imgs = [page_image(f"page {i + 1}") for i in range(pages)]
    imgs[0].save(path, "PDF", save_all=True, append_images=imgs[1:])
    return path


def png_bytes(text="x") -> bytes:
    buf = io.BytesIO()
    page_image(text).save(buf, "PNG")
    return buf.getvalue()
