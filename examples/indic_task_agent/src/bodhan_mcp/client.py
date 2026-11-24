"""Provider-neutral dataclass interface for Bodhan model operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TranscribeResult:
    text: str


@dataclass(frozen=True)
class TranslateResult:
    text: str
    truncated: bool = False


@dataclass(frozen=True)
class SpeechResult:
    audio_bytes: bytes
    format: str = "wav"


@dataclass(frozen=True)
class OcrResult:
    markdown: str
    blocks: tuple[dict, ...] = ()


class BodhanClient(Protocol):
    def transcribe(self, audio_path: str | Path, language: str | None = None) -> TranscribeResult:
        ...

    def translate(
        self,
        text: str,
        target_language: str,
        source_language: str | None = None,
        script: str = "native",
        max_output_tokens: int | None = None,
    ) -> TranslateResult:
        ...

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        style: str | None = None,
    ) -> SpeechResult:
        ...

    def ocr(self, image_path: str | Path) -> OcrResult:
        ...
