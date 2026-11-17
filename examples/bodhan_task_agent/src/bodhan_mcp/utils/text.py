from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_END = ".!?।॥۔؟"
_UNIT_RE = re.compile(rf"[^{_SENTENCE_END}\n]*(?:[{_SENTENCE_END}]+|\n|$)[ \t]*\n*")
_TABLE_LINE_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*\r?\n?$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(```|~~~)")
_DELIMITER_CELL_RE = re.compile(r"^:?-+:?$")
_CLAUSE_RE = re.compile(r"[^,;:،؛]*(?:[,;:،؛]+|$)\s*")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_HEADING_LINE_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]", re.MULTILINE)
_LIST_LINE_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]", re.MULTILINE)


@dataclass(frozen=True)
class TableShape:
    rows: int
    columns: int
    has_delimiter: bool
    ragged: bool


def _units(text: str) -> list[str]:
    return [m.group(0) for m in _UNIT_RE.finditer(text) if m.group(0)]


def _hard_split(unit: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    while len(unit) > max_chars:
        cut = unit.rfind(" ", 0, max_chars)
        cut = max_chars if cut <= 0 else cut + 1
        parts.append(unit[:cut])
        unit = unit[cut:]
    if unit:
        parts.append(unit)
    return parts


def _blocks(text: str) -> list[tuple[str, bool]]:
    blocks: list[tuple[str, bool]] = []
    current: list[str] = []
    current_is_table = False
    open_fence: str | None = None
    for line in text.splitlines(keepends=True):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if open_fence is None:
                open_fence = marker
            elif marker == open_fence:
                open_fence = None
            is_table = False
        else:
            is_table = open_fence is None and bool(_TABLE_LINE_RE.match(line))
        if current and is_table != current_is_table:
            blocks.append(("".join(current), current_is_table))
            current = []
        current.append(line)
        current_is_table = is_table
    if current:
        blocks.append(("".join(current), current_is_table))
    return blocks


def _block_units(block: str, is_table: bool, max_chars: int) -> list[str]:
    if not is_table:
        return _units(block)
    if len(block) <= max_chars:
        return [block]
    return block.splitlines(keepends=True)


def split_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    chunks: list[str] = []
    current = ""
    for block, is_table in _blocks(text):
        for unit in _block_units(block, is_table, max_chars):
            for piece in _hard_split(unit, max_chars):
                if current and len(current) + len(piece) > max_chars:
                    chunks.append(current)
                    current = ""
                current += piece
    if current:
        chunks.append(current)
    return chunks


def _speech_units(text: str, max_chars: int) -> list[str]:
    units: list[str] = []
    for paragraph in _PARAGRAPH_RE.split(text):
        flat = " ".join(paragraph.split())
        for sentence in _units(flat):
            if len(sentence) <= max_chars:
                units.append(sentence)
                continue
            for clause in (m.group(0) for m in _CLAUSE_RE.finditer(sentence) if m.group(0)):
                units.extend(_hard_split(clause, max_chars))
    return [unit.strip() for unit in units if unit.strip()]


def split_for_speech(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    chunks: list[str] = []
    current = ""
    for unit in _speech_units(text, max_chars):
        if current and len(current) + 1 + len(unit) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = f"{current} {unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks


def preview_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = max(text.rfind(" ", 0, limit + 1), text.rfind("\n", 0, limit + 1))
    return text[: cut if cut > 0 else limit]


def split_edges(chunk: str) -> tuple[str, str, str]:
    core = chunk.strip()
    if not core:
        return chunk, "", ""
    start = chunk.index(core)
    return chunk[:start], core, chunk[start + len(core) :]


def _split_cells(line: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    backslashes = 0
    for char in line:
        if char == "|" and backslashes % 2 == 0:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        backslashes = backslashes + 1 if char == "\\" else 0
    cells.append("".join(current).strip())
    return cells


def _cells(line: str) -> list[str]:
    cells = _split_cells(line.strip())
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _is_delimiter_row(cells: list[str]) -> bool:
    return bool(cells) and all(_DELIMITER_CELL_RE.match(cell) for cell in cells)


def _table_rows(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for block, is_table in _blocks(text):
        if not is_table:
            continue
        rows = [_cells(line) for line in block.splitlines() if line.strip()]
        if len(rows) >= 2 and _is_delimiter_row(rows[1]):
            tables.append(rows)
    return tables


def table_shapes(text: str) -> list[TableShape]:
    shapes: list[TableShape] = []
    for rows in _table_rows(text):
        body = [cells for i, cells in enumerate(rows) if i != 1]
        shapes.append(
            TableShape(
                rows=len(body),
                columns=len(rows[0]),
                has_delimiter=True,
                ragged=len({len(cells) for cells in rows}) > 1,
            )
        )
    return shapes


def oversized_table_rows(text: str, max_chars: int) -> int:
    return sum(
        1
        for block, is_table in _blocks(text)
        if is_table
        for line in block.splitlines(keepends=True)
        if len(line) > max_chars
    )


def _delimiter_lines(text: str) -> list[str | None]:
    found: list[str | None] = []
    for block, is_table in _blocks(text):
        if not is_table:
            continue
        lines = [line for line in block.splitlines() if line.strip()]
        found.append(lines[1] if len(lines) >= 2 and _is_delimiter_row(_cells(lines[1])) else None)
    return found


def restore_table_delimiters(source: str, translated: str) -> str:
    originals = _delimiter_lines(source)
    if not any(originals):
        return translated
    rebuilt: list[str] = []
    table_index = 0
    for block, is_table in _blocks(translated):
        if not is_table:
            rebuilt.append(block)
            continue
        original = originals[table_index] if table_index < len(originals) else None
        table_index += 1
        lines = block.splitlines(keepends=True)
        content = [i for i, line in enumerate(lines) if line.strip()]
        if original is not None and len(content) >= 2:
            target = content[1]
            current = lines[target]
            if _is_delimiter_row(_cells(current)) and len(_cells(current)) == len(_cells(original)):
                ending = current[len(current.rstrip("\r\n")) :]
                lines[target] = original.strip() + ending
        rebuilt.append("".join(lines))
    return "".join(rebuilt)


def _structure_counts(text: str) -> tuple[int, int]:
    prose = "".join(block for block, is_table in _blocks(text) if not is_table)
    return len(_HEADING_LINE_RE.findall(prose)), len(_LIST_LINE_RE.findall(prose))


def check_structure(source: str, translated: str) -> list[str]:
    (src_headings, src_items), (out_headings, out_items) = _structure_counts(source), _structure_counts(translated)
    warnings: list[str] = []
    if src_headings != out_headings:
        warnings.append(f"The source has {src_headings} Markdown heading(s) but the translation has {out_headings}.")
    if src_items != out_items:
        warnings.append(f"The source has {src_items} list item(s) but the translation has {out_items}.")
    return warnings


def check_tables(source: str, translated: str) -> list[str]:
    before = table_shapes(source)
    after = table_shapes(translated)
    if len(before) != len(after):
        return [f"The source has {len(before)} Markdown table(s) but the translation has {len(after)}."]
    warnings: list[str] = []
    for index, (src, out) in enumerate(zip(before, after, strict=True), start=1):
        if out.columns != src.columns:
            warnings.append(f"Table {index}: {src.columns} columns in the source, {out.columns} in the translation.")
        if out.rows != src.rows:
            warnings.append(f"Table {index}: {src.rows} rows in the source, {out.rows} in the translation.")
        if out.ragged and not src.ragged:
            warnings.append(f"Table {index}: rows in the translation have differing column counts.")
    return warnings
