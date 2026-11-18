"""Every Pydantic model a tool returns. One place, so the schemas cannot drift."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MediaItem(BaseModel):
    path: str = Field(description="Absolute path of the file as given or found.")
    kind: Literal["audio", "video", "image", "pdf", "text", "unknown"] = Field(description="What the file is.")
    mime: str = Field(description="Best-guess MIME type.")
    size_bytes: int = Field(description="File size on disk.")
    duration_s: float | None = Field(default=None, description="Duration for audio/video, from ffprobe.")
    sample_rate: int | None = Field(default=None, description="Audio sample rate in Hz.")
    channels: int | None = Field(default=None, description="Audio channel count.")
    pages: int | None = Field(default=None, description="Page count for PDFs.")
    has_audio: bool | None = Field(default=None, description="Whether a video file carries an audio track.")
    script: str | None = Field(default=None, description="Dominant writing system of a text file.")
    language_hint: str | None = Field(default=None, description="ISO language code when the script makes it unambiguous.")
    est_asr_cost_inr: float | None = Field(default=None, description="Estimated transcribe cost in rupees, rounded up. Free to read.")
    warnings: list[str] = Field(default_factory=list, description="Problems found while probing, e.g. no_audio_track, silent, encrypted.")


class MediaInventory(BaseModel):
    items: list[MediaItem] = Field(description="One entry per input path, in the order given; a bad path still gets an entry.")
    total_duration_s: float = Field(description="Sum of audio/video durations found.")


class LanguageGuess(BaseModel):
    language: str | None = Field(description="ISO code when the writing system is decisive, else null.")
    script: str = Field(description="Dominant Unicode script, e.g. Tamil, Devanagari, Latin, Mixed.")
    confidence: float = Field(description="0.0-1.0. Low for scripts shared by several languages.")
    candidates: list[str] = Field(default_factory=list, description="Plausible ISO codes, e.g. hi/mr/ne/sa for Devanagari.")
    chars_sampled: int = Field(description="Characters inspected.")


class Chunk(BaseModel):
    index: int = Field(description="0-based window index.")
    start_s: float = Field(description="Window start in the source audio.")
    end_s: float = Field(description="Window end in the source audio.")
    chars: int = Field(description="Characters transcribed in this window.")
    cut_at: Literal["pause", "hard"] = Field(description="Whether the window ends at a detected pause or a forced cut.")


class Transcript(BaseModel):
    text_path: str = Field(description="Path to the UTF-8 transcript. The text itself is in this file.")
    language_hint: str | None = Field(description="Language identified locally from the transcript's script, not from the API.")
    duration_s: float = Field(description="Audio duration transcribed.")
    chunks: list[Chunk] = Field(description="Windows used, with timestamps for subtitle timing.")
    cost_inr: float = Field(description="Measured cost in rupees.")
    cache_hits: int = Field(description="Windows served from cache this call.")
    warnings: list[str] = Field(default_factory=list, description="e.g. partial_failure, hard_cut_2, no_speech.")


class PageResult(BaseModel):
    index: int = Field(description="0-based page index.")
    chars: int = Field(description="Characters extracted from this page.")
    mean_detection_conf: float | None = Field(default=None, description="Mean block detection confidence, 0-1.")
    cost_inr: float = Field(description="Cost of this page.")
    cache_hit: bool = Field(description="Served from cache.")
    retried_max_tokens: bool = Field(description="Retried with a larger per-block token allowance.")
    warnings: list[str] = Field(default_factory=list)


class OcrDocument(BaseModel):
    markdown_path: str = Field(description="Path to the extracted Markdown. Structure and tables are in this file.")
    blocks_path: str | None = Field(default=None, description="Path to per-page layout blocks as JSON (order, label, confidence, bbox).")
    page_count: int = Field(description="Pages extracted.")
    pages: list[PageResult] = Field(description="Per-page results, in document order.")
    cost_inr: float = Field(description="Measured cost in rupees.")
    warnings: list[str] = Field(default_factory=list, description="e.g. low_confidence_page_3, blank_page_2, table_retried_at_4096.")


class Translation(BaseModel):
    text_path: str = Field(description="Path to the translated UTF-8 file.")
    source_language_hint: str | None = Field(description="Locally detected source script/language, when decisive.")
    target_language: str = Field(description="Target ISO code used.")
    segments: int = Field(description="Source segments translated, in order.")
    requests: int = Field(description="Model requests made (> segments after a split).")
    source_chars: int = Field(description="Characters in the source file.")
    output_chars: int = Field(description="Characters in the translation.")
    output_tokens: int = Field(description="Output tokens reported by the API; drives cost.")
    ratio: float = Field(description="output_chars / source_chars.")
    truncated: bool = Field(description="True if the API reported the translation cut short.")
    cost_inr: float = Field(description="Measured cost in rupees.")
    warnings: list[str] = Field(default_factory=list, description="e.g. split_at_heading_3, retranslated_truncated_segment.")


class Speech(BaseModel):
    audio_path: str = Field(description="Path to the generated audio file. AI-generated (Indic-Speak).")
    duration_s: float = Field(description="Measured audio duration.")
    chars: int = Field(description="Characters synthesised.")
    clips: int = Field(description="Number of model requests joined.")
    voice: str = Field(description="Voice name used.")
    language: str = Field(description="Language code requested.")
    format: str = Field(description="wav or mp3.")
    cost_inr: float = Field(description="TTS cost in rupees; never includes a verification call.")
    warnings: list[str] = Field(default_factory=list, description="e.g. duration_off_target, style_unavailable.")


class WrittenText(BaseModel):
    text_path: str = Field(description="Path to the English draft.")
    chars: int = Field(description="Characters written.")
    language: str = Field(description="Always en; non-English output is rejected.")
    truncated: bool = Field(description="Trimmed to the character ceiling at a sentence boundary.")
