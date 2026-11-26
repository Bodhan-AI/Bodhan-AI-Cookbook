"""Split translatable text without handing table syntax to the model."""

import re
from dataclasses import dataclass
from html import escape, unescape
from html.parser import HTMLParser
from typing import Literal
from xml.etree import ElementTree

MAX_SOURCE_BYTES = 128_000
CHUNK_BYTES = 1_200

# Keep fenced blocks, inline code, and link targets literal. HTML tables from
# Indic-OCR may contain merged cells whose attributes must survive translation.
_BLOCK = re.compile(
    r"(?P<fence>^[ \t]*```[^\n]*\n.*?^[ \t]*```[^\n]*(?=\n|$))"
    r"|(?P<table><table\b[^>]*>.*?</table\s*>)",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)
_HTML_TAG = re.compile(r"""(<(?:[^<>"']|"[^"]*"|'[^']*')+>)""")
_INLINE = re.compile(r"(\n{2,}|`+[^`]+?`+|(?<=\])\([^\n)]+\)|https?://[^\s<>)]+|\b[A-Z]{1,8}[0-9]{1,12}\b)")
_TABLE_TOKEN = re.compile(r"`+[^`]+?`+|(?<!\\)\|")
_ALIGNMENT = re.compile(r"\s*:?-{3,}:?\s*")
_LINE_PREFIX = re.compile(r"^([ \t]*(?:#{1,6}[ \t]+|>[ \t]*|(?:[-+*]|\d+[.)])[ \t]+)?)")
_NON_PROSE = re.compile(r"(?:[-*_]{3,}|[\d.,:+−%/\s]+)")


@dataclass(frozen=True)
class Part:
    value: str
    mode: Literal["text", "pipe", "html"] | None = None

    def for_request(self) -> str:
        return unescape(self.value) if self.mode == "html" else self.value

    def render(self, translated: str) -> str:
        translated = translated.strip()
        if self.mode == "html":
            return escape(translated, quote=False)
        if self.mode == "pipe":
            return translated.replace("\n", " ").replace("|", r"\|")
        return translated


def _bounded(value: str, mode: Literal["text", "pipe", "html"]) -> list[Part]:
    parts = []
    while value:
        # A UTF-8 prefix ends at a full code point. Prefer a sentence or word
        # boundary inside that prefix, carrying the separator into the result.
        encoded = value.encode("utf-8")
        if len(encoded) <= CHUNK_BYTES:
            parts.append(Part(value, mode))
            break
        prefix = encoded[:CHUNK_BYTES].decode("utf-8", errors="ignore")
        boundaries = list(re.finditer(r"(?<=[.!?।॥])\s+", prefix))
        if not boundaries or boundaries[-1].start() < len(prefix) // 2:
            boundaries = list(re.finditer(r"\s+", prefix))
        if boundaries:
            split = boundaries[-1]
            if split.start():
                parts.append(Part(value[:split.start()], mode))
            parts.append(Part(value[split.start():split.end()]))
            value = value[split.end():]
        else:
            parts.append(Part(prefix, mode))
            value = value[len(prefix):]
    return parts


def _text(value: str, mode: Literal["text", "pipe", "html"]) -> list[Part]:
    parts = []
    for index, piece in enumerate(_INLINE.split(value)):
        if not piece:
            continue
        if index % 2 or not piece.strip() or _NON_PROSE.fullmatch(piece.strip()):
            parts.append(Part(piece))
            continue
        leading = len(piece) - len(piece.lstrip())
        trailing = len(piece.rstrip())
        if leading:
            parts.append(Part(piece[:leading]))
        parts.extend(_bounded(piece[leading:trailing], mode))
        if trailing < len(piece):
            parts.append(Part(piece[trailing:]))
    return parts


def _pipe_fields(line: str) -> list[str]:
    fields = []
    start = 0
    for token in _TABLE_TOKEN.finditer(line):
        if token[0] == "|":
            fields.extend((line[start:token.start()], "|"))
            start = token.end()
    fields.append(line[start:])
    return fields


def _table_rows(lines: list[str]) -> set[int]:
    table_lines = set()
    for index, line in enumerate(lines):
        cells = [cell for cell in _pipe_fields(line)[::2] if cell.strip()]
        if "|" not in line or not cells or not all(_ALIGNMENT.fullmatch(cell) for cell in cells):
            continue
        table_lines.add(index)
        for direction in (-1, 1):
            row = index + direction
            while 0 <= row < len(lines) and len(_pipe_fields(lines[row])) > 1 and lines[row].strip():
                table_lines.add(row)
                row += direction

    return table_lines


def _markdown(value: str) -> list[Part]:
    lines = value.splitlines(keepends=True)
    table_lines = _table_rows(lines)
    parts = []
    for index, line in enumerate(lines):
        if index in table_lines:
            for field, value in enumerate(_pipe_fields(line)):
                if field % 2 or _ALIGNMENT.fullmatch(value):
                    parts.append(Part(value))
                else:
                    parts.extend(_text(value, "pipe"))
        else:
            prefix = _LINE_PREFIX.match(line)[0]
            if prefix:
                parts.append(Part(prefix))
            parts.extend(_text(line[len(prefix):], "text"))
    return parts


def document_parts(text: str) -> list[Part]:
    parts = []
    end = 0
    for block in _BLOCK.finditer(text):
        parts.extend(_markdown(text[end:block.start()]))
        if block.lastgroup == "fence":
            parts.append(Part(block.group()))
        else:
            for index, token in enumerate(_HTML_TAG.split(block.group())):
                if index % 2:
                    parts.append(Part(token))
                else:
                    parts.extend(_text(token, "html"))
        end = block.end()
    parts.extend(_markdown(text[end:]))
    return parts


def _pipe_table(html: str) -> str:
    try:
        table = ElementTree.fromstring(html)
    except ElementTree.ParseError:
        return html
    for node in table.iter():
        if node.attrib or node.tag.lower() not in {"table", "thead", "tbody", "tr", "th", "td"}:
            return html
        if node.tag.lower() not in {"th", "td"} and (node.text or "").strip():
            return html
        if (node.tail or "").strip():
            return html

    rows = []
    for group in table:
        tag = group.tag.lower()
        if tag == "tr":
            rows.append(group)
        elif tag in {"thead", "tbody"} and all(row.tag.lower() == "tr" for row in group):
            rows.extend(group)
        else:
            return html
    if not rows or not len(rows[0]):
        return html

    width = len(rows[0])
    for index, row in enumerate(rows):
        expected = "th" if index == 0 else "td"
        if len(row) != width or any(cell.tag.lower() != expected or len(cell) for cell in row):
            return html

    def row_line(values: list[str]) -> str:
        return "| " + " | ".join(values) + " |"

    lines = []
    for row in rows:
        cells = []
        for cell in row:
            value = " ".join((cell.text or "").split())
            value = escape(value, quote=False).replace("\\", "\\\\").replace("|", r"\|")
            cells.append(value)
        lines.append(row_line(cells))
    lines.insert(1, row_line(["---"] * width))
    return "\n".join(lines)


def markdown_for_export(text: str) -> str:
    """Use Markdown tables when the HTML layout can be represented faithfully."""
    return _BLOCK.sub(
        lambda block: _pipe_table(block.group()) if block.lastgroup == "table" else block.group(),
        text,
    )


class _SpokenTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[str] = []
        self._cells: list[str] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"td", "th", "caption"}:
            self._end_cell()
            self._cell = []
        elif self._cell is not None and tag in {"br", "p", "div", "li"}:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th", "caption", "tr", "table"}:
            self._end_cell()
        if tag in {"caption", "tr", "table"} and self._cells:
            self.rows.append(", ".join(self._cells))
            self._cells.clear()

    def _end_cell(self) -> None:
        if self._cell is not None:
            cell = " ".join("".join(self._cell).split())
            if cell:
                self._cells.append(cell)
            self._cell = None


def _speak_html_table(block: re.Match[str]) -> str:
    if block.lastgroup != "table":
        return block.group()
    parser = _SpokenTable()
    parser.feed(block.group())
    parser.close()
    return "\n".join(parser.rows)


def text_for_speech(text: str) -> str:
    """Read HTML and Markdown table cells in order without their markup."""
    plain = _BLOCK.sub(_speak_html_table, text)
    lines = plain.splitlines()
    table_lines = _table_rows(lines)
    if plain == text and not table_lines:
        return text
    spoken = []
    for index, line in enumerate(lines):
        if index not in table_lines:
            spoken.append(re.sub(r"^[ ]{0,3}#{1,6}[ \t]+", "", line))
            continue
        cells = [cell.strip() for cell in _pipe_fields(line)[::2] if cell.strip()]
        if not all(_ALIGNMENT.fullmatch(cell) for cell in cells):
            spoken.append(", ".join(cell.replace(r"\|", "|") for cell in cells))
    return "\n".join(spoken)
