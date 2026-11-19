"""Shared pytest fixtures and mock implementations for Bodhan MCP & Agent test suite."""
from __future__ import annotations

import io
import math
import os
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure dummy API keys in test environment
os.environ["LLM_TRANSCRIBE"] = "sk-test-transcribe"
os.environ["LLM_TRANSLATE"] = "sk-test-translate"
os.environ["LLM_OCR"] = "sk-test-ocr"
os.environ["LLM_AUDIO"] = "sk-test-audio"
os.environ["GEMINI_API_KEY"] = "sk-test-gemini"
os.environ["LLM_API_KEY"] = "sk-test-llm"


# ── Synthetic Media Helpers ───────────────────────────────────────────────────

def make_wav(duration_s: float, sample_rate: int = 16000) -> bytes:
    """Generate in-memory mono 16-bit PCM WAV audio bytes (440Hz sine wave)."""
    frames = int(max(0.0, duration_s) * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        for i in range(frames):
            v = int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate))
            w.writeframes(struct.pack("<h", v))
    return buf.getvalue()


MINIMAL_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

MINIMAL_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 15>>stream\n"
    b"BT /F1 12 Tf ET\n"
    b"endstream\nendobj\n"
    b"xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000056 00000 n \n0000000111 00000 n \n0000000212 00000 n \n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n276\n%%EOF\n"
)


# ── Fixtures: Directories & Media ─────────────────────────────────────────────

@pytest.fixture
def temp_out_dir(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "artifacts").mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture
def sample_audio_1s(tmp_path: Path) -> str:
    path = tmp_path / "sample_1s.wav"
    path.write_bytes(make_wav(1.0))
    return str(path)


@pytest.fixture
def sample_audio_30s(tmp_path: Path) -> str:
    path = tmp_path / "sample_30s.wav"
    path.write_bytes(make_wav(30.0))
    return str(path)


@pytest.fixture
def sample_audio_31s(tmp_path: Path) -> str:
    path = tmp_path / "sample_31s.wav"
    path.write_bytes(make_wav(31.0))
    return str(path)


@pytest.fixture
def sample_audio_60s(tmp_path: Path) -> str:
    path = tmp_path / "sample_60s.wav"
    path.write_bytes(make_wav(60.0))
    return str(path)


@pytest.fixture
def sample_image(tmp_path: Path) -> str:
    path = tmp_path / "sample_img.png"
    path.write_bytes(MINIMAL_PNG_BYTES)
    return str(path)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> str:
    path = tmp_path / "sample_doc.pdf"
    path.write_bytes(MINIMAL_PDF_BYTES)
    return str(path)


# Backward-compatible aliases
sample_wav_path = sample_audio_1s
sample_png_path = sample_image


# ── Fake Bodhan Clients ───────────────────────────────────────────────────────

class FakeTranscribeClient:
    """Mock for TranscribeClient recording all calls and returning scripted transcripts."""

    def __init__(self, default_text: str = "नमस्ते यह एक परीक्षण ऑडियो प्रतिलेखन है।", default_lang: str = "hi"):
        self.default_text = default_text
        self.default_lang = default_lang
        self.calls: List[Dict[str, Any]] = []

    async def transcribe_chunk(
        self,
        file_path: str,
        language: Optional[str] = None,
        vad: bool = True,
    ) -> Dict[str, Any]:
        self.calls.append({"file_path": file_path, "language": language, "vad": vad})
        lang = language or self.default_lang
        return {
            "text": self.default_text,
            "language": lang,
            "duration": 1.0,
        }


class FakeTranslateClient:
    """Mock for TranslateClient returning deterministic tagged translations."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def translate_text(
        self,
        text: str,
        target_language_code: str,
        target_script: str = "native",
        source_language_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.calls.append({
            "text": text,
            "target_language_code": target_language_code,
            "target_script": target_script,
            "source_language_code": source_language_code,
        })
        translated = f"[{target_language_code}] {text}"
        tokens = max(1, len(text.split()))
        return {
            "translated_text": translated,
            "output_tokens": tokens,
        }


class FakeSpeakClient:
    """Mock for SpeakClient returning valid WAV bytes scaled by text length."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def synthesize_chunk(
        self,
        text: str,
        voice: str,
        lang: str,
    ) -> bytes:
        self.calls.append({"text": text, "voice": voice, "lang": lang})
        # Length in seconds proportional to character count (approx 1s per 20 chars, min 0.5s)
        duration_s = max(0.5, len(text) / 20.0)
        return make_wav(duration_s)


class FakeOCRClient:
    """Mock for OCRClient returning scripted OCR text."""

    def __init__(self, default_text: str = "दस्तावेज़ का पाठ: यह ओसीआर द्वारा निकाला गया पाठ है।"):
        self.default_text = default_text
        self.calls: List[Dict[str, Any]] = []

    async def ocr_image_file(self, image_path: str) -> str:
        self.calls.append({"image_path": image_path})
        return self.default_text

    async def ocr_image_bytes(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        self.calls.append({"image_bytes_len": len(image_bytes), "mime_type": mime_type})
        return self.default_text


class FakeLLM:
    """Mock LLM for agent planning, interpretation, and judgments."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.scripted_plan: Optional[List[Dict[str, Any]]] = None

    def set_plan(self, plan: List[Dict[str, Any]]) -> None:
        self.scripted_plan = plan

    async def generate(self, prompt: str, system: Optional[str] = None) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        # If a scripted plan exists, serialize it as JSON
        if self.scripted_plan is not None:
            import json
            return json.dumps(self.scripted_plan)
        return '{"goal": "Process input files", "steps": []}'


# ── Client Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def fake_transcribe_client() -> FakeTranscribeClient:
    return FakeTranscribeClient()


@pytest.fixture
def fake_translate_client() -> FakeTranslateClient:
    return FakeTranslateClient()


@pytest.fixture
def fake_speak_client() -> FakeSpeakClient:
    return FakeSpeakClient()


@pytest.fixture
def fake_ocr_client() -> FakeOCRClient:
    return FakeOCRClient()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
