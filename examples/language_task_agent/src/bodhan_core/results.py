"""Typed output envelopes shared with third-party MCP clients."""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(Result):
    code: str
    message: str


class ErrorResult(Result):
    ok: Literal[False]
    error: ErrorDetail


class FileInfo(Result):
    ref: str
    name: str
    bytes: int
    extension: str


class FilesResult(Result):
    ok: Literal[True]
    files: list[FileInfo]


class ReadResult(Result):
    ok: Literal[True]
    text: str
    total_chars: int
    next_offset: int | None
    trust: Literal["UNTRUSTED_DATA"]
    language_evidence: str | None


class WriteResult(Result):
    ok: Literal[True]
    ref: str
    characters: int


class TranscriptResult(WriteResult):
    segments_ref: str
    language: str | None
    language_source: Literal["user_hint", "distinctive_script", "unknown"]
    timing: str


class Verification(Result):
    # Nested evidence differs between round-trip, coverage and speech chunks.
    # Top-level decisions are always typed, never a freeform success string.
    kind: Literal["translation", "audio"]
    ref: str
    checked: bool
    passed: bool
    checks: list[dict] | None = None
    chunks: list[dict] | None = None
    method: str | None = None
    table_structure_preserved: bool | None = None


class TranslationResult(WriteResult):
    verification: Verification
    warning: str | None


class SpeechResult(Result):
    ok: Literal[True]
    ref: str
    ai_generated: Literal[True]
    duration_seconds: float
    verification: Verification
    voice: str
    warning: str | None


class OCRResult(WriteResult):
    pages: int
    layout_ref: str
    language_evidence: str | None
    trust: Literal["UNTRUSTED_DATA"]


class SubtitlesResult(Result):
    ok: Literal[True]
    ref: str
    verification: Verification
    segments: int
    timing: str


OUTPUT_MODELS = {
    "list_files": FilesResult,
    "read_text": ReadResult,
    "write_english": WriteResult,
    "write_translation_index": TranslationResult,
    "transcribe": TranscriptResult,
    "translate": TranslationResult,
    "synthesize": SpeechResult,
    "ocr": OCRResult,
    "subtitles": SubtitlesResult,
}
