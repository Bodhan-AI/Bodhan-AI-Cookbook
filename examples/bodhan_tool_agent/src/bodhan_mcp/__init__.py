#!/usr/bin/env python3
from __future__ import annotations

from .models import (
    SUPPORTED_LANGUAGES,
    SUPPORTED_SPEECH_LANGUAGES,
    SPEECH_VOICES,
    CAPABILITIES,
    MediaInspectResult,
    check_language_supported,
    get_voice_for_language,
    COST_PER_MINUTE_STT,
    COST_PER_10K_OUTPUT_TOKENS_TRANSLATE,
    COST_PER_10K_CHARS_SPEAK,
    COST_PER_IMAGE_OCR,
    BodhanError,
)

from .client import Client, InrLedger, BodhanCache, client
from .media import (
    inspect_audio_video,
    detect_script,
    unicode_script,
    convert_to_wav_16khz_mono,
    chunk_wav,
    chunk_video,
    split_sentences,
    chunk_markdown_table,
    pdf_to_pngs,
    stitch_wav,
    count_table_rows,
    count_table_cols,
)

__all__ = [
    "SUPPORTED_LANGUAGES",
    "SUPPORTED_SPEECH_LANGUAGES",
    "SPEECH_VOICES",
    "CAPABILITIES",
    "MediaInspectResult",
    "check_language_supported",
    "get_voice_for_language",
    "COST_PER_MINUTE_STT",
    "COST_PER_10K_OUTPUT_TOKENS_TRANSLATE",
    "COST_PER_10K_CHARS_SPEAK",
    "COST_PER_IMAGE_OCR",
    "BodhanError",
    "Client",
    "InrLedger",
    "BodhanCache",
    "client",
    "inspect_audio_video",
    "detect_script",
    "unicode_script",
    "convert_to_wav_16khz_mono",
    "chunk_wav",
    "chunk_video",
    "split_sentences",
    "chunk_markdown_table",
    "pdf_to_pngs",
    "stitch_wav",
    "count_table_rows",
    "count_table_cols",
]
