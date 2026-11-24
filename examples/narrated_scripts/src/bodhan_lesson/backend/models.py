"""Records exchanged by pipeline stages."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

LanguageMethod = Literal["override", "script", "llm", "unresolved"]


@dataclass(frozen=True)
class Page:
    """The input index is one-based. A PDF page offset is zero-based."""

    input_index: int
    path: Path
    pdf_offset: int | None = None

    @property
    def page_id(self) -> str:
        return f"p{self.input_index:03d}"


Size = tuple[int, int]
Box = tuple[float, float, float, float]
Affine = tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(frozen=True)
class ImageOperation:
    name: str
    applied: bool
    reason: str
    degrees_ccw: float | None = None
    exif_orientation: int | None = None


@dataclass(frozen=True)
class PageTransform:
    """Dimensions are (width, height). The affine maps normalized edges to original edges."""

    original_size: Size
    normalized_size: Size
    normalized_to_original: Affine
    operations: tuple[ImageOperation, ...]
    coordinate_system: str = "top-left pixel edges; x right, y down"


@dataclass(frozen=True)
class PreprocessedPage:
    """Paths are relative to the run output directory."""

    page_id: str
    status: Literal["ok", "failed"]
    original_path: str | None = None
    normalized_path: str | None = None
    input_sha256: str | None = None
    output_sha256: str | None = None
    transform: PageTransform | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Usage:
    quantity: int | float | None
    unit: str
    rate_inr: float | None
    cost_inr: float | None
    basis: Literal["measured", "estimated", "unknown"]
    reason: str | None = None


@dataclass(frozen=True)
class Attempt:
    id: str
    service: str
    model: str
    entity_ids: tuple[str, ...]
    backend: str
    number: int
    result: Literal["ok", "failed"]
    elapsed_ms: int
    error_category: str | None
    reason: str | None
    fallback_reason: str | None
    usage: Usage


BlockKind = Literal[
    "heading", "paragraph", "list", "table", "equation", "figure", "caption",
    "header", "footer", "page_number", "footnote", "sidebar", "code", "advertisement",
]


@dataclass(frozen=True)
class TableCell:
    text: str
    is_header: bool | None = None
    rowspan: int = 1
    colspan: int = 1


@dataclass(frozen=True)
class ListItem:
    marker: str | None
    text: str
    indent: int = 0


@dataclass(frozen=True)
class OCRBlock:
    """The initial ID survives reordering. Boxes use the normalized page raster."""

    id: str
    kind: BlockKind
    text: str
    provider_label: str | None = None
    provider_type: str | None = None
    provider_order: int | None = None
    bbox: Box | None = None
    confidence: float | None = None
    flags: tuple[str, ...] = ()
    heading_level: int | None = None
    items: tuple[ListItem, ...] | None = None
    rows: tuple[tuple[TableCell, ...], ...] | None = None
    latex: str | None = None
    caption_for: str | None = None


@dataclass(frozen=True)
class OCRPage:
    page_id: str
    input_index: int
    status: Literal["ok", "partial", "failed"]
    raw_response: dict | None = None
    attempts: tuple[Attempt, ...] = ()
    attempt_ids: tuple[str, ...] = ()
    blocks: tuple[OCRBlock, ...] = ()
    raw_block_ids: tuple[str, ...] = ()
    ordered_block_ids: tuple[str, ...] = ()
    image_path: str | None = None
    image_digest: str | None = None
    reason: str | None = None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoundaryDecision:
    """An action on original OCR block IDs, listed in reading order."""

    action: Literal["exclude", "keep", "join_prose", "join_table"]
    page_ids: tuple[str, ...]
    block_ids: tuple[str, ...]
    reason: str
    drop_repeated_header: bool | None = None


@dataclass(frozen=True)
class SourceBlock:
    """A lesson block retains its first OCR ID and all contributing IDs in order."""

    id: str
    kind: BlockKind
    source_block_ids: tuple[str, ...]
    text: str = ""
    heading_level: int | None = None
    items: tuple[ListItem, ...] | None = None
    rows: tuple[tuple[TableCell, ...], ...] | None = None
    latex: str | None = None
    caption_for: str | None = None
    eligible: Literal[True] = field(init=False, default=True)

    def item_unit_id(self, item: int) -> str:
        """Address a one-based list item."""
        return f"{self.id}:i{item:02d}"

    def cell_unit_id(self, row: int, column: int) -> str:
        """Address a cell by one-based row and cell ordinals."""
        return f"{self.id}:r{row:02d}:c{column:02d}"


@dataclass(frozen=True)
class UnavailableContent:
    """A gap occupies a place in reading order without supplying lesson text."""

    id: str
    page_id: str
    reason: str
    source_block_ids: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    caption_for: str | None = None
    kind: Literal["unavailable"] = field(init=False, default="unavailable")
    eligible: Literal[False] = field(init=False, default=False)


ChapterEntry = SourceBlock | UnavailableContent


@dataclass(frozen=True)
class SourceSection:
    """IDs count sections in chapter order. The intro has no heading or parent."""

    id: str
    heading_level: int | None
    heading_block_id: str | None
    parent_id: str | None
    blocks: tuple[ChapterEntry, ...]


@dataclass(frozen=True)
class SourceChapter:
    source_lang: str | None
    sections: tuple[SourceSection, ...]


Glossary = dict[str, str | dict[str, str]]


@dataclass(frozen=True)
class SegmentAddress:
    """Ordinals address the canonical block's structural slots and start at one."""

    block_id: str
    kind: BlockKind | Literal["list_item", "table_cell"]
    item: int | None = None
    row: int | None = None
    column: int | None = None


@dataclass(frozen=True)
class TranslationUnit:
    """Split parts share a structural address and retain its unsplit ID as parent_id."""

    id: str
    address: SegmentAddress
    section_id: str
    group_id: str
    source_block_ids: tuple[str, ...]
    payload: str
    protected_spans: dict[str, str] = field(default_factory=dict)
    literal: bool = False
    parent_id: str | None = None
    part: int | None = None
    join_after: str = ""
    status: Literal["ready", "failed"] = "ready"
    reason: str | None = None


@dataclass(frozen=True)
class TranslationPlan:
    source_lang: str | None
    glossary: Glossary
    context_limit: int
    payload_budget: int
    units: tuple[TranslationUnit, ...]


TranslationRoute = Literal["reuse", "literal", "direct", "pivot"]
LanguageStatus = Literal["completed", "completed_with_misses", "failed"]


@dataclass(frozen=True)
class PlaceholderCheck:
    expected: dict[str, int]
    observed: dict[str, int]
    status: Literal["passed", "failed"]


@dataclass(frozen=True)
class TranslationLeg:
    source_lang: str
    target_lang: str
    attempt_ids: tuple[str, ...]
    protected_payload: str | None = None
    placeholder_check: PlaceholderCheck | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TermCheck:
    source_term: str
    expected_term: str
    expected_count: int
    observed_count: int
    applied_count: int
    status: Literal["seen", "missing"]


@dataclass(frozen=True)
class TranslatedUnit:
    unit_id: str
    address: SegmentAddress
    section_id: str
    group_id: str
    source_block_ids: tuple[str, ...]
    route: TranslationRoute | None
    protected_spans: dict[str, str]
    parent_id: str | None
    part: int | None
    join_after: str
    text: str | None
    reason: str | None
    legs: tuple[TranslationLeg, ...] = ()
    pivot_unit_id: str | None = None
    terminology: tuple[TermCheck, ...] = ()


@dataclass(frozen=True)
class TargetTranslation:
    source_lang: str | None
    target_lang: str
    status: LanguageStatus
    reason: str | None
    units: tuple[TranslatedUnit, ...]


@dataclass(frozen=True)
class TranslationRun:
    targets: tuple[TargetTranslation, ...]
    pivots: tuple[TranslatedUnit, ...]

    @property
    def failed_languages(self) -> tuple[str, ...]:
        return tuple(target.target_lang for target in self.targets if target.status == "failed")


@dataclass(frozen=True)
class TranslationIssue:
    check: str
    reason: str
    expected: object
    observed: object


@dataclass(frozen=True)
class UnitTranslationQA:
    unit_id: str
    address: SegmentAddress
    source_block_ids: tuple[str, ...]
    eligible: bool
    text: str | None
    disposition: Literal["accepted", "recovered", "exhausted", "unavailable"]
    issues: tuple[TranslationIssue, ...] = ()
    retry_issues: tuple[TranslationIssue, ...] = ()
    retry_attempt_ids: tuple[str, ...] = ()
    parent_id: str | None = None
    part: int | None = None
    join_after: str = ""
    terminology: tuple[TermCheck, ...] = ()


@dataclass(frozen=True)
class SectionTranslationQA:
    section_id: str
    status: LanguageStatus
    reason: str | None
    units: tuple[UnitTranslationQA, ...]
    unavailable_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class TargetTranslationQA:
    source_lang: str | None
    target_lang: str
    status: LanguageStatus
    reason: str | None
    sections: tuple[SectionTranslationQA, ...]
    issues: tuple[TranslationIssue, ...] = ()


@dataclass(frozen=True)
class CheckedLesson:
    qa: TargetTranslationQA
    markdown: str


@dataclass(frozen=True)
class TranslationQARun:
    """Checked target lessons and reusable English pivots that passed structure QA."""

    lessons: tuple[CheckedLesson, ...]
    pivot_repairs: tuple[TranslatedUnit, ...]
    english_pivots: tuple[TranslatedUnit, ...] = ()

    @property
    def failed_languages(self) -> tuple[str, ...]:
        return tuple(lesson.qa.target_lang for lesson in self.lessons if lesson.qa.status == "failed")


NarrationAction = Literal["reuse_translation", "translate_english", "skip"]


@dataclass(frozen=True)
class NarrationEntry:
    """One reading decision. Text is checked target text or grounded English for translation."""

    section_id: str
    block_id: str | None
    source_block_ids: tuple[str, ...]
    policy: str
    action: NarrationAction
    unit_ids: tuple[str, ...] = ()
    text: str | None = None
    evidence: str | None = None
    reason: str | None = None
    supporting_block_ids: tuple[str, ...] = ()
    fallback_text: str | None = None


@dataclass(frozen=True)
class TargetNarrationPlan:
    target_lang: str
    translation_status: LanguageStatus
    entries: tuple[NarrationEntry, ...]


@dataclass(frozen=True)
class NarrationPlan:
    source_lang: str | None
    targets: tuple[TargetNarrationPlan, ...]

    @property
    def failed_languages(self) -> tuple[str, ...]:
        return tuple(target.target_lang for target in self.targets if target.translation_status == "failed")


@dataclass(frozen=True)
class ScriptIssue:
    block_id: str | None
    source_block_ids: tuple[str, ...]
    unit_ids: tuple[str, ...]
    policy: str
    reason: str
    disposition: Literal["skipped", "used_fallback"] = "skipped"
    translation_attempt_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpokenNumber:
    """A checked numeric value and the target words used to speak it."""

    value: str
    text: str


@dataclass(frozen=True)
class ScriptChunk:
    """ID is the section ID plus its one-based chunk ordinal. Text is the exact TTS input."""

    id: str
    section_id: str
    source_block_ids: tuple[str, ...]
    unit_ids: tuple[str, ...]
    text: str
    policy: str
    translation_attempt_ids: tuple[str, ...] = ()
    duration_seconds: float | None = None
    speech_qa: str | None = None
    spoken_numbers: tuple[SpokenNumber, ...] = ()


@dataclass(frozen=True)
class ScriptSection:
    section_id: str
    chunks: tuple[ScriptChunk, ...]
    issues: tuple[ScriptIssue, ...]
    reason: str | None = None
    duration_seconds: float | None = None
    loudness_lufs: float | None = None
    speech_qa: str | None = None
    audio_path: str | None = None


@dataclass(frozen=True)
class NarrationScript:
    language: str
    voice: str | None
    max_chars: int
    status: LanguageStatus
    sections: tuple[ScriptSection, ...]


@dataclass(frozen=True)
class NarrationScripts:
    scripts: tuple[NarrationScript, ...]

    @property
    def failed_languages(self) -> tuple[str, ...]:
        return tuple(script.language for script in self.scripts if script.status == "failed")


@dataclass(frozen=True)
class SpeechCandidate:
    """One synthesis outcome for a script chunk. Paths are relative to the run output."""

    chunk_id: str
    section_id: str
    voice: str
    requested_characters: int
    status: Literal["ok", "failed"]
    attempt_ids: tuple[str, ...] = ()
    attempt_id: str | None = None
    audio_path: str | None = None
    format: str | None = None
    duration_seconds: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TargetSpeech:
    language: str
    voice: str
    status: LanguageStatus
    chunks: tuple[SpeechCandidate, ...]
    reason: str | None = None


@dataclass(frozen=True)
class SpeechRun:
    targets: tuple[TargetSpeech, ...]

    @property
    def failed_languages(self) -> tuple[str, ...]:
        return tuple(target.language for target in self.targets if target.status == "failed")


@dataclass(frozen=True)
class SpeechEdit:
    operation: Literal["insert", "delete", "substitute"]
    reference_start: int
    transcript_start: int
    reference_text: str
    transcript_text: str


@dataclass(frozen=True)
class SpeechComparison:
    reference: str
    normalized_reference: str
    transcript: str | None
    normalized_transcript: str | None
    reference_characters: int
    insertions: int | None = None
    deletions: int | None = None
    substitutions: int | None = None
    total_edits: int | None = None
    cer: float | None = None
    edits: tuple[SpeechEdit, ...] = ()
    signals: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class SpeechQAAttempt:
    phase: Literal["original", "regenerated", "split"]
    candidate: SpeechCandidate
    comparison: SpeechComparison
    transcribe_attempt_ids: tuple[str, ...]
    outcome: Literal["passed", "suspect", "unavailable"]
    reason: str | None = None


@dataclass(frozen=True)
class ChunkSpeechQA:
    chunk_id: str
    reference_characters: int
    attempts: tuple[SpeechQAAttempt, ...]
    accepted_candidate_ids: tuple[str, ...]
    status: Literal["accepted", "exhausted", "unavailable"]
    reason: str | None = None

    @property
    def accepted_candidates(self) -> tuple[SpeechCandidate, ...]:
        """Return verified audio in playback order, including complete split repairs."""
        passed = {attempt.candidate.attempt_id: attempt.candidate for attempt in self.attempts
                  if attempt.outcome == "passed"}
        return tuple(passed[attempt_id] for attempt_id in self.accepted_candidate_ids if attempt_id in passed)


@dataclass(frozen=True)
class SpeechQAMetrics:
    total_chunks: int
    measured_chunks: int
    total_reference_characters: int
    covered_reference_characters: int
    measured_reference_characters: int
    total_edits: int | None
    cer: float | None
    coverage: float | None
    reason: str | None = None


@dataclass(frozen=True)
class SectionSpeechQA:
    section_id: str
    chunks: tuple[ChunkSpeechQA, ...]
    metrics: SpeechQAMetrics
    reason: str | None = None


@dataclass(frozen=True)
class TargetSpeechQA:
    language: str
    cer_threshold: float
    status: LanguageStatus
    sections: tuple[SectionSpeechQA, ...]
    metrics: SpeechQAMetrics
    reason: str | None = None


@dataclass(frozen=True)
class SpeechQARun:
    targets: tuple[TargetSpeechQA, ...]

    @property
    def failed_languages(self) -> tuple[str, ...]:
        return tuple(target.language for target in self.targets if target.status == "failed")


@dataclass(frozen=True)
class ChapterMarker:
    section_id: str
    title: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SectionAudio:
    section_id: str
    title: str
    audio_path: str | None = None
    duration_seconds: float | None = None
    pcm_frames: int | None = None
    loudness_lufs: float | None = None
    loudness_status: Literal["within_target", "outside_target", "unavailable"] = "unavailable"
    loudness_reason: str | None = None
    reason: str | None = None
    omitted_chunk_ids: tuple[str, ...] = ()

    @property
    def coverage_status(self) -> Literal["narrated", "partial", "skipped"]:
        if self.audio_path is None:
            return "skipped"
        return "partial" if self.omitted_chunk_ids else "narrated"


@dataclass(frozen=True)
class TargetAudio:
    language: str
    status: LanguageStatus
    sections: tuple[SectionAudio, ...]
    audio_path: str | None = None
    duration_seconds: float | None = None
    chapters: tuple[ChapterMarker, ...] = ()
    reason: str | None = None
    coverage: float | None = None


@dataclass(frozen=True)
class AudioRun:
    targets: tuple[TargetAudio, ...]

    @property
    def failed_languages(self) -> tuple[str, ...]:
        return tuple(target.language for target in self.targets if target.status == "failed")


@dataclass(frozen=True)
class LanguageSample:
    page_id: str
    status: Literal["readable", "blank", "failed"]
    script_counts: dict[str, int]
    reason: str | None = None


@dataclass(frozen=True)
class LanguageDecision:
    code: str | None
    method: LanguageMethod
    sample_page_ids: tuple[str, ...]
    reason: str
    attempts: tuple[Attempt, ...] = ()
    samples: tuple[LanguageSample, ...] = ()
