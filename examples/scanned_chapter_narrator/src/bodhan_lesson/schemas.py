"""Strongly-typed Document Intermediate Representation (IR) schemas for Bodhan Lesson."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    PAGE_HEADER = "PageHeader"
    PAGE_FOOTER = "PageFooter"
    SECTION_HEADER = "SectionHeader"
    PARAGRAPH = "Paragraph"
    TABLE = "Table"
    EQUATION = "Equation"
    FIGURE = "Picture"
    FIGURE_CAPTION = "PictureCaption"
    LIST_ITEM = "ListItem"
    UNKNOWN = "Unknown"


class OCRBlock(BaseModel):
    """Normalized OCR block preserving raw metadata, bounding box, and provenance."""

    block_id: str
    order: int = 0
    page_number: int
    raw_type: str = ""
    raw_label: str = ""
    normalized_type: BlockType = BlockType.UNKNOWN
    text: str = ""
    raw_latex: str | None = None
    bbox_xyxy: list[float] | None = None  # [x0, y0, x1, y1] normalized or pixel coords
    confidence: float | None = None
    is_doubtful: bool = False
    doubt_reason: str | None = None


class PageResult(BaseModel):
    """Page-level OCR result capturing raw markdown and constituent layout blocks."""

    page_number: int
    image_path: Path
    raw_markdown: str = ""
    blocks: list[OCRBlock] = Field(default_factory=list)
    detected_headers: list[str] = Field(default_factory=list)
    detected_footers: list[str] = Field(default_factory=list)
    is_doubtful: bool = False
    doubt_reason: str | None = None


class LessonSection(BaseModel):
    """A cohesive pedagogical section assembled across page boundaries."""

    section_id: str
    section_slug: str
    title: str
    level: int = 1
    blocks: list[OCRBlock] = Field(default_factory=list)
    source_markdown: str = ""
    equations: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    figure_captions: list[str] = Field(default_factory=list)
    word_count: int = 0


class DocumentIR(BaseModel):
    """Document Intermediate Representation (IR) linking raw scans to assembled lesson."""

    source_language: str = "en"
    total_pages: int
    pages: list[PageResult] = Field(default_factory=list)
    sections: list[LessonSection] = Field(default_factory=list)
    source_markdown: str = ""
    glossary: dict[str, str] = Field(default_factory=dict)
    flagged_blocks_count: int = 0


class NarrationItem(BaseModel):
    """Pedagogical narration script and audio delivery record for one section."""

    section_id: str
    section_slug: str
    section_title: str
    english_script: str = ""
    target_script: str = ""
    target_language: str = ""
    grounding_references: list[str] = Field(default_factory=list)
    audio_path: Path | None = None
    full_track_offset_seconds: float = 0.0
    duration_seconds: float = 0.0
    loudness_lufs: float | None = None
    qa_metrics: dict[str, Any] = Field(default_factory=dict)


class QAReportSection(BaseModel):
    """Granular quality assurance check results for one section."""

    section_id: str
    language: str
    coverage_score: float = 1.0  # 1.0 = 100% of sentences spoken
    repetition_detected: bool = False
    repetition_phrases: list[str] = Field(default_factory=list)
    cer: float = 0.0  # Character Error Rate via Indic-Transcribe
    wer: float = 0.0  # Word Error Rate
    passed: bool = True
    retry_count: int = 0
    failure_notes: str | None = None


class RunReport(BaseModel):
    """Comprehensive machine-readable run report conforming to assignment requirements."""

    status: str = "completed"
    source_language: str = "en"
    target_languages: list[str] = Field(default_factory=list)
    total_pages: int = 0
    total_sections: int = 0
    total_cost_inr: float = 0.0
    total_duration_seconds: float = 0.0
    cost_by_model: dict[str, float] = Field(default_factory=dict)
    page_ocr_checks: list[dict[str, Any]] = Field(default_factory=list)
    section_translation_checks: list[dict[str, Any]] = Field(default_factory=list)
    narration_qa_checks: list[QAReportSection] = Field(default_factory=list)
    token_integrity_checks: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
