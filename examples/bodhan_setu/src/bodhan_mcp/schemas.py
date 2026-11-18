"""Per-tool structured result contracts advertised by MCP outputSchema."""

from __future__ import annotations

from typing_extensions import TypedDict


class CostEstimate(TypedDict, total=False):
    model: str
    quantity: float
    unit: str
    estimated_cost_inr: float
    rate: dict[str, float]
    quantity_basis: str


class FileReference(TypedDict, total=False):
    file_id: str
    media_type: str
    size_bytes: int
    expires_in_seconds: int


class PageArtifact(FileReference, total=False):
    page: int
    file_path: str
    width: int
    height: int


class AudioChunk(FileReference, total=False):
    index: int
    file_path: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float


class SelectedVoice(TypedDict, total=False):
    voice: str
    language: str
    gender: str
    distance: float | None
    similarity_score: float | None
    method: str
    match_note: str


class CommonResult(TypedDict, total=False):
    dry_run: bool
    will_call_api: bool
    model: str
    estimated_cost_inr: float
    quantity: float
    unit: str
    rate: dict[str, float]
    quantity_basis: str
    chunk_count: int
    cost_inr: float
    cost_note: str
    cache_hit: bool


class ConvertPdfResult(CommonResult, total=False):
    source_file: str
    output_dir: str
    page_count: int
    dpi: int
    image_format: str
    pages: list[PageArtifact]


class ChunkAudioResult(CommonResult, total=False):
    source_file: str
    output_dir: str
    duration_seconds: float
    smart_chunking: bool
    max_chunk_seconds: int
    vad_method: str | None
    chunks: list[AudioChunk]


class TranscribeAudioResult(CommonResult, total=False):
    text: str
    source_file: str
    duration_seconds: float
    chunks: int
    language_code_supplied: str | None
    language_mode: str
    language_detection_note: str | None
    file_id: str
    media_type: str
    size_bytes: int
    expires_in_seconds: int


class TranscribeVideoResult(TranscribeAudioResult, total=False):
    source_video: str


class ExtractVideoAudioResult(CommonResult, total=False):
    source_video: str
    audio_file: str
    duration_seconds: float
    format: str
    file_id: str
    media_type: str
    size_bytes: int
    expires_in_seconds: int


class ReplaceVideoAudioResult(CommonResult, total=False):
    output_file: str
    size_bytes: int
    video_duration_seconds: float
    replacement_audio_duration_seconds: float
    duration_strategy: str
    file_id: str
    media_type: str
    expires_in_seconds: int
    source_video: str
    replacement_audio: str


class TranslateVideoResult(CommonResult, total=False):
    source_video: str
    output_file: str
    size_bytes: int
    video_duration_seconds: float
    replacement_audio_duration_seconds: float
    duration_strategy: str
    duration_seconds: float
    selected_voice: SelectedVoice
    cost_estimates: list[CostEstimate]
    known_estimated_total_inr: float
    estimated_total_inr: float
    estimate_complete: bool
    estimate_note: str | None
    transcript: str
    translation: str
    target_language: str
    steps: dict[str, object]
    file_id: str
    media_type: str
    expires_in_seconds: int


class TranslateTextResult(CommonResult, total=False):
    translation: str
    target_language: str
    script: str
    truncated: bool
    source_language_mode: str
    chunk_limit_chars: int
    translated_chunks: int
    file_id: str
    media_type: str
    size_bytes: int
    expires_in_seconds: int
    translation_truncated: bool
    translation_total_characters: int


class TransliterateTextResult(CommonResult, total=False):
    transliteration: str
    language: str
    script: str
    truncated: bool
    file_id: str
    media_type: str
    size_bytes: int
    expires_in_seconds: int
    transliteration_truncated: bool
    transliteration_total_characters: int


class SynthesizeSpeechResult(CommonResult, total=False):
    output_file: str
    size_bytes: int
    model: str
    ai_generated: bool
    chunks: int
    characters: int
    file_id: str
    media_type: str
    expires_in_seconds: int


class OcrDocumentResult(CommonResult, total=False):
    output_file: str
    characters: int
    pages: int
    script_mode: str
    detected_language: str | None
    language_detection_note: str | None
    page_count: int
    file_id: str
    media_type: str
    size_bytes: int
    expires_in_seconds: int


class GetFileResult(TypedDict, total=False):
    file_ref: str
    media_type: str
    size_bytes: int
    is_text: bool
    download_url: str | None
    note: str
    total_characters: int
    offset: int
    content: str
    has_more: bool
