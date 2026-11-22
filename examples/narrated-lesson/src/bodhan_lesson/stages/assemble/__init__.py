"""Stage 4: assemble cleaned pages into `lesson.src.md`.

`lesson.src.md` is a faithful, lightly-cleaned transcript of the pages. It never holds
generated text (figure descriptions, narration, rewrites); those live in sidecar files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bodhan_lesson.models import Element, Page, Section
from bodhan_lesson.stages.assemble.elements import build_elements
from bodhan_lesson.stages.assemble.sections import build_sections
from bodhan_lesson.stages.assemble.tables import join_split_tables


@dataclass
class Lesson:
    markdown: str
    elements: list[Element]
    sections: list[Section]
    table_joins: int

    def element_map(self) -> list[dict[str, Any]]:
        """Element -> blocks -> page -> line range: how any sentence is traced back to the scan."""
        return [
            {"id": e.id, "kind": e.kind, "role": e.role, "level": e.level, "section": e.section_id,
             "page": e.page, "blocks": e.block_ids, "doubtful": e.doubtful, "paired_with": e.paired_with, "math_issues": e.math_issues,
             "lines": [e.line_start, e.line_end]}
            for e in self.elements
        ]


def _render(elements: list[Element], sections: list[Section]) -> str:
    """Join elements section by section, recording each element's 1-based line range."""
    by_id = {e.id: e for e in elements}
    chunks: list[str] = []
    line = 1
    for section in sections:
        for element_id in section.element_ids:
            element = by_id[element_id]
            height = element.markdown.count("\n") + 1
            element.line_start, element.line_end = line, line + height - 1
            chunks.append(element.markdown)
            line += height + 1  # one blank line between elements
    return "\n\n".join(chunks) + "\n"


def assemble(pages: list[Page], figure_dir_name: str = "figures") -> Lesson:
    elements = build_elements(pages, figure_dir_name)
    table_joins = join_split_tables(elements)
    sections = build_sections(elements)
    by_id = {e.id: e for e in elements}
    ordered = [by_id[element_id] for section in sections for element_id in section.element_ids]
    return Lesson(_render(ordered, sections), ordered, sections, table_joins)
