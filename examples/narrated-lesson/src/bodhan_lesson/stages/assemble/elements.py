"""Blocks -> Markdown elements, chosen by OCR label and type (never by text patterns)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bodhan_lesson.models import Block, Element, Page
from bodhan_lesson.stages.assemble import math_markdown as mathmd
from bodhan_lesson.stages.cleanup.page_rules import is_figure

HEADING_LEVELS = {"Chapter-title": 1, "Section-title": 2, "Sub-section-title": 3, "Sub-sub-section-title": 4}
TYPE_HEADING_LEVELS = {"Title": 1, "SectionHeader": 2}
ROLES = {"Solved-example": "solved_example", "Example": "solved_example", "Question": "question",
         "Answer": "answer", "MCQ": "mcq"}
CAPTION_LABELS = frozenset({"Image-caption", "Table-caption"})

CAPTION_MAX_GAP = 0.15  # of page height
CAPTION_MIN_OVERLAP = 0.30  # of the narrower box's width
CAPTION_BELOW_BIAS = 0.05  # of page height: penalty for pairing a caption with a figure *below* it
CAPTION_ROW_TOLERANCE_PX = 30  # captions whose tops are this close read as one row, left to right

# A new line starting like this is real structure, so the hard wrap before it is kept.
_STRUCTURAL_LINE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\([a-zivx]{1,4}\)\s|\$\$|\||>|#)")


def is_caption(block: Block) -> bool:
    return block.type == "Caption" or block.label in CAPTION_LABELS


def unwrap_lines(text: str) -> str:
    """Join OCR hard line wraps inside a paragraph, keeping list items and display math on their own lines."""
    lines = text.split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if _STRUCTURAL_LINE.match(line):
            out.append(line)
        else:
            out[-1] = f"{out[-1].rstrip()} {line.lstrip()}"
    return "\n".join(out)


def _single_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def heading_level(block: Block) -> int | None:
    return HEADING_LEVELS.get(block.label) or TYPE_HEADING_LEVELS.get(block.type)


# ---- caption pairing ---------------------------------------------------------
def _vertical_gap(a: list[float], b: list[float]) -> float:
    """Distance between two boxes along y; 0 when they overlap."""
    return max(0.0, a[1] - b[3], b[1] - a[3])


def _horizontal_overlap(a: list[float], b: list[float]) -> float:
    overlap = min(a[2], b[2]) - max(a[0], b[0])
    return max(0.0, overlap) / max(1.0, min(a[2] - a[0], b[2] - b[0]))


def _pairing_cost(caption: Block, figure: Block, page_height: int) -> float:
    """Gap between the boxes, penalised when the figure sits below the caption.

    Captions normally sit under their figure, so with two figures stacked in a column a
    caption squeezed between them belongs to the one above.
    """
    cost = _vertical_gap(caption.bbox_xyxy, figure.bbox_xyxy)
    if figure.bbox_xyxy[1] >= caption.bbox_xyxy[3]:
        cost += CAPTION_BELOW_BIAS * page_height
    return cost


def pair_captions(page: Page) -> dict[str, list[Block]]:
    """Map figure block id -> its captions. A figure may own several (sub-captions plus 'Fig. 6.1')."""
    figures = [b for b in page.blocks if is_figure(b)]
    paired: dict[str, list[Block]] = {}
    for caption in (b for b in page.blocks if is_caption(b)):
        candidates = [
            (_pairing_cost(caption, f, page.height), f)
            for f in figures
            if _vertical_gap(caption.bbox_xyxy, f.bbox_xyxy) <= CAPTION_MAX_GAP * page.height
            and _horizontal_overlap(caption.bbox_xyxy, f.bbox_xyxy) >= CAPTION_MIN_OVERLAP
        ]
        if candidates:
            paired.setdefault(min(candidates, key=lambda c: c[0])[1].id, []).append(caption)
    for captions in paired.values():
        captions.sort(key=lambda c: (c.bbox_xyxy[1] // CAPTION_ROW_TOLERANCE_PX, c.bbox_xyxy[0]))
    return paired


# ---- element construction ----------------------------------------------------
@dataclass
class _Builder:
    elements: list[Element]

    def add(self, block: Block, kind: str, markdown: str, **extra: object) -> Element:
        element = Element(
            id=f"e{len(self.elements) + 1:04d}", kind=kind, markdown=markdown, page=block.page,
            block_ids=[block.id], label=block.label, doubtful=block.doubtful, **extra,  # type: ignore[arg-type]
        )
        if block.doubtful:
            element.markdown = f"<!-- doubtful {block.id}: {block.doubtful_reason} -->\n{markdown}"
        self.elements.append(element)
        return element


def _figure_link(block: Block, figure_dir_name: str) -> str:
    name = block.figure_path.replace("\\", "/").rsplit("/", 1)[-1] if block.figure_path else f"{block.id}.png"
    return f"![]({figure_dir_name}/{name})"


def build_elements(pages: list[Page], figure_dir_name: str = "figures") -> list[Element]:
    """Convert cleaned pages into one ordered element stream."""
    builder = _Builder([])
    for page in pages:
        paired = pair_captions(page)
        paired_ids = {c.id for captions in paired.values() for c in captions}
        for block in page.blocks:
            if block.id in paired_ids:
                continue  # emitted right after its figure
            if is_figure(block):
                figure = builder.add(block, "figure", _figure_link(block, figure_dir_name))
                for caption in paired.get(block.id, []):
                    builder.add(caption, "caption", f"*{_single_line(caption.text)}*", paired_with=figure.id)
                continue
            _add_block(builder, block)
    for element in builder.elements:
        _ensure_valid_math(element)
    return builder.elements


def _ensure_valid_math(element: Element) -> None:
    """Repair what is safely repairable; fence and flag anything a renderer would reject."""
    problems = mathmd.math_problems(element.markdown)
    if not problems:
        return
    if "nested_dollar" in problems:
        element.markdown = mathmd.repair_nested_dollars(element.markdown)
        element.math_issues.append("repaired:nested_dollar")
        problems = mathmd.math_problems(element.markdown)
    if not problems:
        return
    element.math_issues += problems
    element.doubtful = True
    marker = f"<!-- doubtful {element.block_ids[0]}: math_unparsable:{','.join(problems)} -->"
    if element.kind == "heading":
        element.markdown = f"{marker}\n{element.markdown}"
    else:  # a fenced block is never parsed as math, so the preview cannot break
        element.markdown = f"{marker}\n```text\n{element.markdown}\n```"


def _add_block(builder: _Builder, block: Block) -> None:
    text = block.text.strip()
    if not text:
        return
    if is_caption(block):
        builder.add(block, "caption", f"*{_single_line(text)}*")
    elif (level := heading_level(block)) is not None:
        _add_heading(builder, block, level, _single_line(text))
    elif block.type == "Table":
        builder.add(block, "table", text)
    elif block.type == "Equation":
        builder.add(block, "equation", mathmd.shape_equation(text))
    elif block.label == "Footnote":
        builder.add(block, "footnote", unwrap_lines(text))
    elif block.label == "Infobox":
        quoted = "\n".join(f"> {line}" for line in unwrap_lines(text).split("\n"))
        builder.add(block, "note", quoted)
    elif block.label == "List":
        builder.add(block, "list", text)
    else:
        builder.add(block, "paragraph", unwrap_lines(text), role=ROLES.get(block.label))


def _add_heading(builder: _Builder, block: Block, level: int, text: str) -> None:
    """Consecutive chapter-title blocks ("CHAPTER 6", "LINES AND ANGLES") become one heading."""
    previous = builder.elements[-1] if builder.elements else None
    if previous and previous.kind == "heading" and previous.label == block.label == "Chapter-title" \
            and not previous.doubtful and not block.doubtful:
        previous.markdown = f"{previous.markdown} {text}"
        previous.block_ids.append(block.id)
        return
    builder.add(block, "heading", f"{'#' * level} {text}", level=level)
