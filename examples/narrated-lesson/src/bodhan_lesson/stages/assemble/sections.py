"""Group elements into sections; the unit later used for narration and audio files."""

from __future__ import annotations

import re
import unicodedata

from bodhan_lesson.models import Element, Section

MAX_SECTION_CHARS = 6000
SPLIT_PREFERENCE = 0.5  # a part this full may break early before a ### or deeper heading
SECTION_LEVELS = (1, 2)  # "#" and "##" start a section
SLUG_MAX_LENGTH = 40
MIN_INTRO_CHARS = 60  # shorter text before the first heading is folded into the first section


def slugify(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:SLUG_MAX_LENGTH].strip("-")
    return slug or "section"


def _heading_title(element: Element) -> str:
    return element.markdown.lstrip("#").strip()


def _is_section_heading(element: Element) -> bool:
    return element.kind == "heading" and element.level in SECTION_LEVELS


def _chars(elements: list[Element]) -> int:
    return sum(len(e.markdown) for e in elements)


def _group(elements: list[Element]) -> list[tuple[str, list[Element]]]:
    """Split the stream at each `#`/`##` heading.

    A heading that follows only headings (chapter title, then "6.1 Introduction") joins the
    same group, so no section is just a title; the latest heading names it.
    """
    groups: list[tuple[str, list[Element]]] = []
    for element in elements:
        current = groups[-1] if groups else None
        has_content = bool(current) and any(e.kind != "heading" for e in current[1])
        if _is_section_heading(element) and (current is None or has_content):
            groups.append((_heading_title(element), [element]))
            continue
        if current is None:
            current = ("Introduction", [])
            groups.append(current)
        if _is_section_heading(element) and not has_content:
            groups[-1] = (_heading_title(element), current[1])
        groups[-1][1].append(element)
    return groups


def _fold_tiny_intro(groups: list[tuple[str, list[Element]]]) -> list[tuple[str, list[Element]]]:
    """Orphan text before the first heading (a stray page code, say) must not become its own section."""
    first_title, first_elements = groups[0]
    is_tiny_orphan = (
        first_title == "Introduction"
        and first_elements[0].kind != "heading"
        and _chars(first_elements) < MIN_INTRO_CHARS
    )
    if len(groups) > 1 and is_tiny_orphan:
        title, elements = groups[1]
        return [(title, first_elements + elements), *groups[2:]]
    return groups


def _footnotes_last(elements: list[Element]) -> list[Element]:
    return [e for e in elements if e.kind != "footnote"] + [e for e in elements if e.kind == "footnote"]


def _split_parts(elements: list[Element]) -> list[list[Element]]:
    """Cut an over-long section at element boundaries, preferring a deeper heading."""
    parts: list[list[Element]] = [[]]
    for element in elements:
        size = _chars(parts[-1])
        too_big = size + len(element.markdown) > MAX_SECTION_CHARS
        early = element.kind == "heading" and size >= SPLIT_PREFERENCE * MAX_SECTION_CHARS
        if parts[-1] and (too_big or early):
            parts.append([])
        parts[-1].append(element)
    return parts


def build_sections(elements: list[Element]) -> list[Section]:
    """Assign `section_id` to every element and return the sections in document order."""
    sections: list[Section] = []
    for title, group in _fold_tiny_intro(_group(elements)):
        group = _footnotes_last(group)
        parts = _split_parts(group)
        for number, part in enumerate(parts, start=1):
            index = len(sections) + 1
            part_title = title if number == 1 else f"{title} (part {number})"
            section = Section(
                id=f"s{index:02d}", slug=f"{index:02d}-{slugify(part_title)}", title=part_title,
                element_ids=[e.id for e in part], page_start=min(e.page for e in part),
                page_end=max(e.page for e in part), chars=_chars(part),
            )
            for element in part:
                element.section_id = section.id
            sections.append(section)
    return sections
