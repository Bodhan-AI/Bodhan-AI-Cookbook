"""Model clients. Every translation, TTS and ASR call goes through one of these to a Bodhan
model; the LLM judge uses a separate OpenAI-compatible client (see judge.py).
"""

from __future__ import annotations

from .base import (  # noqa: F401
    BodhanClients,
    SpeakResult,
    TranscribeResult,
    TranslateResult,
)
