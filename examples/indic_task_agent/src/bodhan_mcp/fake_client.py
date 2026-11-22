"""Deterministic offline fake Bodhan client for tests."""

from __future__ import annotations

import re
import wave
from pathlib import Path

from .client import OcrResult, SpeechResult, TranscribeResult, TranslateResult


class FakeBodhanClient:
    """A network-free implementation with deterministic, predictable results."""

    def transcribe(self, audio_path: str | Path, language: str | None = None) -> TranscribeResult:
        try:
            with wave.open(str(audio_path), "rb") as audio:
                marker = audio.readframes(audio.getnframes()).decode("utf-8")
        except (wave.Error, UnicodeDecodeError, OSError):
            return TranscribeResult(text="fake transcribed text")
        if marker.startswith("FAKE-WAV|"):
            chunks = re.findall(r"FAKE-WAV\|[^|\n]*\|([^\n]*)", marker)
            return TranscribeResult(text=" ".join(chunks))
        return TranscribeResult(text="fake transcribed text")

    def translate(
        self,
        text: str,
        target_language: str,
        source_language: str | None = None,
        script: str = "native",
        max_output_tokens: int | None = None,
    ) -> TranslateResult:
        return TranslateResult(text=f"fake translation [{target_language}] {text}")

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        style: str | None = None,
    ) -> SpeechResult:
        content = f"FAKE-WAV|{language}|{text}\n".encode() + b"\0" * 8000
        import io

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(1)
            audio.setframerate(8000)
            audio.writeframes(content)
        return SpeechResult(audio_bytes=buffer.getvalue(), format="wav")

    def ocr(self, image_path: str | Path) -> OcrResult:
        block = {"type": "paragraph", "text": "fake OCR text"}
        return OcrResult(markdown="fake OCR text", blocks=(block,))
