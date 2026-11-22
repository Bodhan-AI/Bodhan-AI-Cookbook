"""Rejoin Markdown tables that continue across a page break."""

from __future__ import annotations

import re

from bodhan_lesson.models import Element

_SEPARATOR_ROW = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _rows(markdown: str) -> list[str]:
    return [line for line in markdown.strip().split("\n") if line.strip()]


def _columns(row: str) -> int:
    return len(row.strip().strip("|").split("|"))


def _normalise(row: str) -> list[str]:
    return [cell.strip().lower() for cell in row.strip().strip("|").split("|")]


def _is_gfm(rows: list[str]) -> bool:
    return len(rows) >= 2 and rows[0].lstrip().startswith("|") and bool(_SEPARATOR_ROW.match(rows[1]))


def _continuation_rows(first: list[str], second: list[str]) -> list[str] | None:
    """Rows to append to `first`, or None if `second` is not its continuation."""
    if not first or not second or _columns(first[0]) != _columns(second[0]):
        return None
    if _is_gfm(second):  # carries its own header: only a repeated header continues the table
        return second[2:] if _normalise(second[0]) == _normalise(first[0]) else None
    return second if second[0].lstrip().startswith("|") else None


def join_split_tables(elements: list[Element]) -> int:
    """Merge a table ending page N with a table starting page N+1. Returns the number of joins."""
    joins = 0
    index = 0
    while index < len(elements) - 1:
        current, following = elements[index], elements[index + 1]
        if current.kind == following.kind == "table" and following.page == current.page + 1 \
                and not current.doubtful and not following.doubtful:
            first, second = _rows(current.markdown), _rows(following.markdown)
            extra = _continuation_rows(first, second) if _is_gfm(first) else None
            if extra is not None:
                current.markdown = "\n".join([*first, *extra])
                current.block_ids += following.block_ids
                del elements[index + 1]
                joins += 1
                continue
        index += 1
    return joins
