"""Render source or translated unit text using the same chapter structure."""

import re
from collections.abc import Mapping, Set
from html import escape

from ..models import ChapterEntry, SourceBlock, SourceChapter, UnavailableContent
from .extraction import MATH_SPAN

# A pipe following an even number of backslashes still needs an escape.
UNESCAPED_PIPE = re.compile(r"(?<!\\)(?:\\\\)*\|")
ORDERED_MARKER = re.compile(r"[0-9]{1,9}[.)]")
MARKDOWN_PUNCTUATION = re.compile(r"([\\\x60*_\[\]<>#~&$])")
LEADING_MARKER = re.compile(r"^(?:(\d{1,9})([.)])(?=\s|$)|([+-])(?=\s|$)|(-)(?=[ -]*$))")


def _plain_markdown(text: str) -> str:
    """Escape translated Markdown controls outside checked inline math."""
    parts = []
    start = 0
    for math in MATH_SPAN.finditer(text):
        parts.append(_escape_plain_fragment(text[start:math.start()]))
        parts.append(math[0])
        start = math.end()
    parts.append(_escape_plain_fragment(text[start:]))
    escaped = "".join(parts).strip()
    return LEADING_MARKER.sub(
        lambda match: (match[1] or "") + "\\" + (match[2] or match[3] or match[4]),
        escaped,
    )


def _escape_plain_fragment(text: str) -> str:
    compact = re.sub(r"[ \t]*[\r\n][ \t\r\n]*", " ", text)
    return MARKDOWN_PUNCTUATION.sub(r"\\\1", compact)


def _slot_text(
    unit_id: str, source: str, text_by_unit: Mapping[str, str], plain_text_units: Set[str],
) -> str:
    text = text_by_unit.get(unit_id, source)
    return _plain_markdown(text) if unit_id in plain_text_units else text


def _markdown_cell(text: str) -> str:
    escaped = UNESCAPED_PIPE.sub(lambda match: match[0][:-1] + r"\|", text)
    return "<br>".join(escaped.splitlines())


def _render_table(block: SourceBlock, text_by_unit: Mapping[str, str], plain_text_units: Set[str]) -> str:
    rows = block.rows or ()
    if not rows:
        return ""
    cells = [
        [text_by_unit.get(block.cell_unit_id(row_index, column), cell.text)
         for column, cell in enumerate(row, 1)]
        for row_index, row in enumerate(rows, 1)
    ]
    width = len(rows[0])
    header_count = sum(cell.is_header is True for cell in rows[0])
    # Use HTML where a GFM table would lose spans or individual header cells.
    requires_html = (
        any(len(row) != width or any(cell.rowspan != 1 or cell.colspan != 1 for cell in row)
            for row in rows)
        or 0 < header_count < width
        or any(cell.is_header is True for row in rows[1:] for cell in row)
    )
    if requires_html:
        lines = ["<table>"]
        for row, values in zip(rows, cells):
            rendered = []
            for cell, value in zip(row, values):
                tag = "th" if cell.is_header else "td"
                attributes = ""
                if cell.rowspan != 1:
                    attributes += f' rowspan="{cell.rowspan}"'
                if cell.colspan != 1:
                    attributes += f' colspan="{cell.colspan}"'
                content = "<br>".join(escape(value, quote=False).splitlines())
                rendered.append(f"<{tag}{attributes}>{content}</{tag}>")
            lines.append("<tr>" + "".join(rendered) + "</tr>")
        lines.append("</table>")
        return "\n".join(lines)

    rendered_rows = []
    for row_index, row in enumerate(cells, 1):
        rendered = [
            _markdown_cell(_plain_markdown(value) if block.cell_unit_id(row_index, column)
                           in plain_text_units else value)
            for column, value in enumerate(row, 1)
        ]
        rendered_rows.append("| " + " | ".join(rendered) + " |")
    if not header_count:
        # A blank Markdown header keeps the first data row in the table body.
        rendered_rows.insert(0, "| " + " | ".join([""] * width) + " |")
    rendered_rows.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(rendered_rows)


def _render_list(block: SourceBlock, text_by_unit: Mapping[str, str], plain_text_units: Set[str]) -> str:
    lines = []
    for index, item in enumerate(block.items or (), 1):
        text = _slot_text(block.item_unit_id(index), item.text, text_by_unit, plain_text_units)
        marker = item.marker or "-"
        if marker not in {"-", "+", "*"} and not ORDERED_MARKER.fullmatch(marker):
            if marker not in {"•", "◦"}:
                text = f"{marker} {text}"
            marker = "-"
        indent = " " * item.indent
        first, *rest = text.split("\n")
        lines.append(f"{indent}{marker} {first}")
        continuation = indent + " " * (len(marker) + 1)
        lines.extend(continuation + line if line else "" for line in rest)
    return "\n".join(lines)


def _render_block(
    block: ChapterEntry, text_by_unit: Mapping[str, str], heading_level: int | None,
    plain_text_units: Set[str],
) -> str:
    if isinstance(block, UnavailableContent):
        item = f"block {block.id}" if block.source_block_ids else f"page {block.page_id}"
        return f"> [Unavailable {item}]"
    if block.kind == "table":
        return _render_table(block, text_by_unit, plain_text_units)
    if block.kind == "list":
        return _render_list(block, text_by_unit, plain_text_units)
    if block.kind == "equation":
        if block.id in plain_text_units and block.id in text_by_unit:
            return _plain_markdown(text_by_unit[block.id])
        return f"$$\n{block.latex}\n$$" if block.latex is not None else ""
    text = _slot_text(block.id, block.text, text_by_unit, plain_text_units)
    if block.kind == "heading":
        title = text if block.id in plain_text_units and "\n" not in text else " ".join(text.split())
        return "#" * (heading_level or 1) + " " + title
    if block.kind == "figure":
        return text or "[Figure]"
    return text


def render_markdown(
    chapter: SourceChapter, text_by_unit: Mapping[str, str] | None = None, *,
    plain_text_units: Set[str] = frozenset(),
) -> str:
    """Render overrides by block, item or cell ID and use source text for other units."""
    overrides = text_by_unit if text_by_unit is not None else {}
    parts = []
    for section in chapter.sections:
        for block in section.blocks:
            rendered = _render_block(block, overrides, section.heading_level, plain_text_units)
            if rendered:
                parts.append(rendered)
    return "\n\n".join(parts) + "\n" if parts else ""
