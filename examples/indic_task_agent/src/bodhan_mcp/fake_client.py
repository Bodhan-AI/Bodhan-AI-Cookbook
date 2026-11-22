"""Deterministic offline fake Bodhan client for tests."""

from __future__ import annotations

from pathlib import Path

from .client import OcrResult, SpeechResult, TranscribeResult, TranslateResult


class FakeBodhanClient:
    """A network-free implementation with deterministic, predictable results."""

    def transcribe(self, audio_path: str | Path, language: str | None = None) -> TranscribeResult:
        return TranscribeResult(text="fake transcribed text")

    def translate(
        self,
        text: str,
        target_language: str,
        source_language: str | None = None,
        script: str = "native",
    ) -> TranslateResult:
        return TranslateResult(text=f"fake translation [{target_language}] {text}")

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        style: str | None = None,
    ) -> SpeechResult:
        payload = f"FAKE-WAV|{language}|{text}".encode("utf-8")
        return SpeechResult(audio_bytes=payload, format="wav")

    def ocr(self, image_path: str | Path) -> OcrResult:
        block = {"type": "paragraph", "text": "fake OCR text"}
        return OcrResult(markdown="fake OCR text", blocks=(block,))
