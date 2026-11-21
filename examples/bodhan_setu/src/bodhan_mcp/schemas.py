"""Per-tool structured result contracts advertised by MCP outputSchema."""

from __future__ import annotations

from typing_extensions import TypedDict


class CostEstimate(TypedDict, total=False):
    model: str | None
    quantity: float | None
    unit: str | None
    estimated_cost_inr: float | None
    rate: dict[str, float] | None
    quantity_basis: str | None


class FileReference(TypedDict, total=False):
    file_id: str | None
    media_type: str | None
    size_bytes: int | None
    expires_in_seconds: int | None


class PageArtifact(FileReference, total=False):
    page: int | None
    file_path: str | None
    width: int | None
    height: int | None


class AudioChunk(FileReference, total=False):
    index: int | None
    file_path: str | None
    start_seconds: float | None
    end_seconds: float | None
    duration_seconds: float | None


class SelectedVoice(TypedDict, total=False):
    voice: str | None
    language: str | None
    gender: str | None
    distance: float | None
    similarity_score: float | None
    method: str | None
    match_note: str | None


class CommonResult(TypedDict, total=False):
    dry_run: bool | None
    will_call_api: bool | None
    model: str | None
    estimated_cost_inr: float | None
    quantity: float | None
    unit: str | None
    rate: dict[str, float] | None
    quantity_basis: str | None
    chunk_count: int | None
    cost_inr: float | None
    cost_note: str | None
    cache_hit: bool | None


class ConvertPdfResult(CommonResult, total=False):
    source_file: str | None
    output_dir: str | None
    page_count: int | None
    dpi: int | None
    image_format: str | None
    pages: list[PageArtifact] | None


class ChunkAudioResult(CommonResult, total=False):
    source_file: str | None
    output_dir: str | None
    duration_seconds: float | None
    smart_chunking: bool | None
    max_chunk_seconds: int | None
    vad_method: str | None
    chunks: list[AudioChunk] | None


class TranscribeAudioResult(CommonResult, total=False):
    text: str | None
    source_file: str | None
    duration_seconds: float | None
    chunks: int | None
    language_code_supplied: str | None
    language_mode: str | None
    language_detection_note: str | None
    file_id: str | None
    media_type: str | None
    size_bytes: int | None
    expires_in_seconds: int | None


class TranscribeVideoResult(TranscribeAudioResult, total=False):
    source_video: str | None


class ExtractVideoAudioResult(CommonResult, total=False):
    source_video: str | None
    audio_file: str | None
    duration_seconds: float | None
    format: str | None
    file_id: str | None
    media_type: str | None
    size_bytes: int | None
    expires_in_seconds: int | None


class ReplaceVideoAudioResult(CommonResult, total=False):
    output_file: str | None
    size_bytes: int | None
    video_duration_seconds: float | None
    replacement_audio_duration_seconds: float | None
    duration_strategy: str | None
    file_id: str | None
    media_type: str | None
    expires_in_seconds: int | None
    source_video: str | None
    replacement_audio: str | None


class TranslateVideoResult(CommonResult, total=False):
    source_video: str | None
    output_file: str | None
    size_bytes: int | None
    video_duration_seconds: float | None
    replacement_audio_duration_seconds: float | None
    duration_strategy: str | None
    duration_seconds: float | None
    selected_voice: SelectedVoice | None
    cost_estimates: list[CostEstimate] | None
    known_estimated_total_inr: float | None
    estimated_total_inr: float | None
    estimate_complete: bool | None
    estimate_note: str | None
    transcript: str | None
    translation: str | None
    target_language: str | None
    steps: dict[str, object] | None
    file_id: str | None
    media_type: str | None
    expires_in_seconds: int | None


class TranslateAudioResult(CommonResult, total=False):
    source_file: str | None
    output_file: str | None
    duration_seconds: float | None
    speech_regions: int | None
    translated_regions: int | None
    target_language: str | None
    selected_voice: SelectedVoice | None
    cost_estimates: list[CostEstimate] | None
    known_estimated_total_inr: float | None
    estimated_total_inr: float | None
    estimate_complete: bool | None
    estimate_note: str | None
    file_id: str | None
    media_type: str | None
    size_bytes: int | None
    expires_in_seconds: int | None
    transcript: str | None
    translation: str | None
    note: str | None


class TranslateTextResult(CommonResult, total=False):
    translation: str | None
    target_language: str | None
    script: str | None
    truncated: bool | None
    source_language_mode: str | None
    chunk_limit_chars: int | None
    translated_chunks: int | None
    file_id: str | None
    media_type: str | None
    size_bytes: int | None
    expires_in_seconds: int | None
    translation_truncated: bool | None
    translation_total_characters: int | None


class TransliterateTextResult(CommonResult, total=False):
    transliteration: str | None
    language: str | None
    script: str | None
    truncated: bool | None
    file_id: str | None
    media_type: str | None
    size_bytes: int | None
    expires_in_seconds: int | None
    transliteration_truncated: bool | None
    transliteration_total_characters: int | None


class SynthesizeSpeechResult(CommonResult, total=False):
    output_file: str | None
    size_bytes: int | None
    model: str | None
    ai_generated: bool | None
    chunks: int | None
    characters: int | None
    file_id: str | None
    media_type: str | None
    expires_in_seconds: int | None


class OcrDocumentResult(CommonResult, total=False):
    output_file: str | None
    characters: int | None
    pages: int | None
    script_mode: str | None
    detected_language: str | None
    language_detection_note: str | None
    page_count: int | None
    file_id: str | None
    media_type: str | None
    size_bytes: int | None
    expires_in_seconds: int | None


class GetFileResult(TypedDict, total=False):
    file_ref: str | None
    media_type: str | None
    size_bytes: int | None
    is_text: bool | None
    download_url: str | None
    note: str | None
    total_characters: int | None
    offset: int | None
    content: str | None
    has_more: bool | None
