"""Data model shared by all stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Block:
    """One Indic-OCR block, plus annotations added by cleanup."""

    id: str  # "p003.b07"
    page: int
    order: int
    label: str  # OCR label, e.g. "Paragraph", "Section-title"
    type: str  # OCR type, e.g. "Text", "Title", "Equation", "Picture"
    bbox_xyxy: list[float]
    conf: float
    text: str
    # --- added by cleanup ---
    doubtful: bool = False
    doubtful_reason: str = ""
    reclassified: bool = False
    eq_label: str | None = None
    is_answer: bool = False
    figure_path: str | None = None
    figure_description: str = ""
    rules_applied: list[str] = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.bbox_xyxy[2] - self.bbox_xyxy[0]

    @property
    def y_min(self) -> float:
        return self.bbox_xyxy[1]

    @classmethod
    def from_ocr(cls, page: int, raw: dict[str, Any]) -> "Block":
        """Build a block from one entry of the OCR response's `blocks` array."""
        order = int(raw["order"])
        return cls(
            id=f"p{page:03d}.b{order:02d}",
            page=page,
            order=order,
            label=str(raw.get("label", "")),
            type=str(raw.get("type", "")),
            bbox_xyxy=[float(v) for v in raw["bbox_xyxy"]],
            conf=float(raw.get("conf", 0.0)),
            text=str(raw.get("text", "")),
        )


@dataclass
class DroppedBlock:
    """A block removed by cleanup, kept for traceability."""

    block: Block
    reason: str


@dataclass
class Element:
    """One Markdown element of `lesson.src.md`, traceable to its OCR blocks."""

    id: str  # "e0042"
    kind: str  # heading | paragraph | list | table | equation | figure | caption | note | footnote
    markdown: str  # exactly what is written to lesson.src.md
    page: int
    block_ids: list[str]
    label: str = ""  # source OCR label
    role: str | None = None  # solved_example | question | answer | mcq
    level: int | None = None  # heading level
    section_id: str = ""
    doubtful: bool = False
    paired_with: str | None = None  # a caption's figure element id
    math_issues: list[str] = field(default_factory=list)  # "repaired:..." or unresolved problems
    line_start: int = 0  # 1-based, in lesson.src.md
    line_end: int = 0


@dataclass
class Section:
    id: str  # "s01"
    slug: str  # "01-pairs-of-angles"
    title: str
    element_ids: list[str] = field(default_factory=list)
    page_start: int = 0
    page_end: int = 0
    chars: int = 0


@dataclass
class Page:
    number: int  # 1-based
    width: int
    height: int
    image_path: str
    blocks: list[Block] = field(default_factory=list)
    markdown: str = ""  # OCR's own page markdown (reference only)
    dropped: list[DroppedBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FigureDescription:
    """What a figure shows, in plain speakable English. Lives in figures.json, never in lesson.src.md."""

    element_id: str
    block_ids: list[str]
    image: str  # path relative to the source directory
    caption: str = ""
    description: str = ""
    decorative: bool = False
    visible_labels: list[str] = field(default_factory=list)
    source: str = "caption_only"  # "vision" | "caption_only"
    warnings: list[str] = field(default_factory=list)


@dataclass
class NarrationUnit:
    """One spoken passage. `text` is exactly what will be translated and sent to the TTS."""

    id: str  # "s04.u03"
    section_id: str
    element_ids: list[str]
    kind: str  # heading | explanation | example | exercise_problem | figure
    text: str
    origin: str = "llm"  # "llm" | "fallback"
    warnings: list[str] = field(default_factory=list)


@dataclass
class SectionNarration:
    section_id: str
    title: str
    slug: str
    units: list[NarrationUnit] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)  # {"element": id, "reason": ...}
