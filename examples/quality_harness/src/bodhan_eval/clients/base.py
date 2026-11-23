"""Client interfaces and result types shared by the real Bodhan clients and the fakes.

All three Bodhan models are wrapped behind `BodhanClients`, which the pipeline receives. The
cache + cost ledger live here so every path (real or fake) is cached and costed identically,
and a cached call always costs 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Note on cost: `cost_inr` is the ACTUAL spend (0 on a cache hit). `logical_cost_inr` is what
# the call would cost regardless of caching -- it is what the (deterministic) report uses, so a
# cached rerun yields a byte-identical report while actual spend is 0.


@dataclass
class TranslateResult:
    text: str
    output_tokens: int
    cost_inr: float
    cached: bool
    logical_cost_inr: float = 0.0


@dataclass
class SpeakResult:
    audio_path: str
    input_chars: int
    cost_inr: float
    cached: bool
    audio_bytes: bytes | None = None
    logical_cost_inr: float = 0.0


@dataclass
class TranscribeResult:
    text: str
    audio_seconds: float
    cost_inr: float
    cached: bool
    logical_cost_inr: float = 0.0


class TranslateClient(Protocol):
    async def translate(self, text: str, source_lang: str, target_lang: str) -> TranslateResult: ...


class SpeakClient(Protocol):
    async def speak(self, text: str, lang: str, voice: str | None = None) -> SpeakResult: ...


class TranscribeClient(Protocol):
    async def transcribe(self, audio_path: str, lang: str) -> TranscribeResult: ...


@dataclass
class BodhanClients:
    translate: TranslateClient
    speak: SpeakClient
    transcribe: TranscribeClient

    async def back_translate(self, text: str, source_lang: str) -> TranslateResult:
        """Back-translation is just translation into English with the same model."""
        return await self.translate.translate(text, source_lang=source_lang, target_lang="en")
