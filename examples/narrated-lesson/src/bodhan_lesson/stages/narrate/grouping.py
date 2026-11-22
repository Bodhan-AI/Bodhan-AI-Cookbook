"""Group lesson elements into narration groups and batch them for the LLM.

A worked example (with its solution) and an exercise problem (with its figure) are narrated
as one unit with their own length cap, so each must reach the LLM in one piece.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bodhan_lesson.models import Element

EXAMPLE, EXERCISE, HEADING, EXPLANATION = "example", "exercise_problem", "heading", "explanation"
_GROUP_STARTS = {"solved_example": EXAMPLE, "question": EXERCISE}


@dataclass
class Group:
    id: int
    kind: str
    elements: list[Element]

    @property
    def chars(self) -> int:
        return sum(len(e.markdown) for e in self.elements)


@dataclass
class Batch:
    """The groups sent in one LLM call, with lookups the validator needs."""

    groups: list[Group]
    extra_text: dict[str, str] = field(default_factory=dict)  # element id -> figure description and caption
    group_of: dict[str, Group] = field(init=False)
    position: dict[str, int] = field(init=False)
    elements: dict[str, Element] = field(init=False)

    def __post_init__(self) -> None:
        self.group_of = {e.id: g for g in self.groups for e in g.elements}
        self.elements = {e.id: e for g in self.groups for e in g.elements}
        self.position = {eid: index for index, eid in enumerate(self.elements)}

    @property
    def ids(self) -> list[str]:
        return list(self.elements)


def group_elements(elements: list[Element]) -> list[Group]:
    """Headings and plain elements stand alone; an example or exercise problem absorbs what
    follows it until the next heading, example or problem."""
    groups: list[Group] = []
    open_group: Group | None = None
    for element in elements:
        kind = _GROUP_STARTS.get(element.role or "")
        if element.kind == "heading":
            open_group = None
            groups.append(Group(len(groups), HEADING, [element]))
        elif kind:
            open_group = Group(len(groups), kind, [element])
            groups.append(open_group)
        elif open_group:
            open_group.elements.append(element)
        else:
            groups.append(Group(len(groups), EXPLANATION, [element]))
    return groups


def make_batches(groups: list[Group], max_chars: int, extra_text: dict[str, str] | None = None) -> list[Batch]:
    """Pack consecutive groups into batches of about `max_chars`; a group is never split."""
    batches: list[list[Group]] = []
    size = 0
    for group in groups:
        if batches and size + group.chars <= max_chars:
            batches[-1].append(group)
            size += group.chars
        else:
            batches.append([group])
            size = group.chars
    return [Batch(b, extra_text or {}) for b in batches]


# ---- figure placement -------------------------------------------------------------------
_FIGURE_NUMBER = re.compile(r"\bFig(?:ure|\.)?\s*(\d+(?:\.\d+)*)", re.IGNORECASE)
_REFERRING_KINDS = frozenset({"paragraph", "list", "note"})


def figure_number(text: str) -> str | None:
    """The first figure number in `text` ("Fig. 6.18" -> "6.18")."""
    match = _FIGURE_NUMBER.search(text)
    return match.group(1) if match else None


def _refers_to(markdown: str, number: str) -> bool:
    return any(m.group(1) == number for m in _FIGURE_NUMBER.finditer(markdown))


def place_figures_at_first_reference(elements: list[Element], captions: dict[str, str]) -> list[Element]:
    """Move each figure to just after the first text that refers to it by number.

    The OCR puts a side-column figure wherever the page layout puts it, often after several
    paragraphs. A teacher describes the figure at the moment it is mentioned, so the narration
    follows the text, not the layout. A figure nobody refers to keeps its place.
    """
    result = list(elements)
    inserted_after: dict[str, int] = {}  # reference element id -> figures already placed behind it
    for figure in [e for e in elements if e.kind == "figure"]:
        number = figure_number(captions.get(figure.id, ""))
        reference = next((e for e in elements if number and e.kind in _REFERRING_KINDS
                          and _refers_to(e.markdown, number)), None)
        if reference is None:
            continue
        result.remove(figure)
        offset = inserted_after.get(reference.id, 0)
        result.insert(result.index(reference) + 1 + offset, figure)
        inserted_after[reference.id] = offset + 1
    return result
