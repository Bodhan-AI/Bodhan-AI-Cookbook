"""Parse Indic-OCR responses and write one record per input page."""

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import asdict, replace
from hashlib import sha256
from html.parser import HTMLParser
from math import isfinite
from pathlib import Path

from ..inference import ProviderError
from ..models import BlockKind, Box, ListItem, OCRBlock, OCRPage, Page, PreprocessedPage, TableCell
from ..report import AttemptLog
from .provider import OCRResponse, OCRResponseError

HEADING_LEVELS = {
    "title": 1, "chapter-title": 1, "section-title": 2,
    "sub-section-title": 3, "sub-sub-section-title": 4,
}
LABEL_KINDS: dict[str, BlockKind] = {
    **dict.fromkeys(HEADING_LEVELS, "heading"),
    "chapter-end-section": "heading", "list": "list", "mcq": "list",
    "table": "table", "equation": "equation", "expression": "equation",
    "figure": "figure", "image": "figure", "diagram": "figure", "chart": "figure",
    "image-caption": "caption", "table-caption": "caption",
    "header": "header", "footer": "footer", "page-number": "page_number",
    "folio": "page_number", "footnote": "footnote", "infobox": "sidebar",
    "code": "code", "advertisement": "advertisement",
}
TYPE_KINDS: dict[str, BlockKind] = {
    "title": "heading", "sectionheader": "heading", "list": "list",
    "table": "table", "equation": "equation", "figure": "figure", "picture": "figure",
    "caption": "caption", "pageheader": "header", "pagefooter": "footer",
    "pagenumber": "page_number", "footnote": "footnote",
}
OPTIONALLY_EMPTY = {"figure", "header", "footer", "advertisement"}
CONTENT_FLAGS = {"malformed_table", "malformed_math", "missing_text"}

ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*)")
SETEXT = re.compile(r"^ {0,3}(=+|-+)\s*$")
LIST_MARKER = re.compile(r"^([ \t]*)([-+*]|\d{1,9}[.)]|\(\d{1,9}\)|[•◦])[ \t]+(.*)")
HTML_TABLE = re.compile(r"<table\b", re.IGNORECASE)
HTML_TABLE_END = re.compile(r"</table\s*>", re.IGNORECASE)
HTML_CAPTION = re.compile(r"<figcaption\b[^>]*>(.*?)</figcaption\s*>", re.I | re.S)
MD_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\([^\n]+\)\s*$")
FIGURE_CAPTION = re.compile(
    r"^\s*(?:(?:fig(?:ure)?\.?|diagram|table|चित्र|सारणी)[ \t]*\d+[\w.-]*)(?=\s|[:।]|$)",
    re.IGNORECASE,
)
DISPLAY_DELIMITERS = (("$$", "$$"), (r"\[", r"\]"))
MATH_DELIMITERS = (*DISPLAY_DELIMITERS, (r"\(", r"\)"), ("$", "$"))
MATH_SPAN = re.compile(
    r"(?<!\\)(?:\$\$(.*?)\$\$|\\\[(.*?)\\\]|\\\((.*?)\\\)|\$(?!\$)(.*?)(?<!\\)\$(?!\$))",
    re.S,
)
MATH_DELIMITER = re.compile(r"(?<!\\)\$|\\[\[\]()]")
TEX_TOKENS = re.compile(r"\\(?:begin|end)\{[^{}]*\}|\\[{}\\]|[{}]")
PIPE = re.compile(r"\\.|[|]")
FENCE = re.compile(r"^\s*(\x60{3,}|~{3,})")
TABLE_RULE = re.compile(r":?-+:?")


class OCRSchemaError(ValueError):
    """The response cannot be interpreted as structured blocks or Markdown."""


def _number(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        return float(value) if isfinite(value) else None
    except OverflowError:
        return None


def _box(value: object) -> Box | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    x0, y0, x1, y1 = (_number(item) for item in value)
    if x0 is None or y0 is None or x1 is None or y1 is None:
        return None
    return (x0, y0, x1, y1) if 0 <= x0 < x1 and 0 <= y0 < y1 else None


def _math(text: str) -> tuple[str, bool]:
    """Remove one enclosing math delimiter and check basic TeX balance."""
    latex = text
    trimmed = text.strip()
    delimited = False
    for start, end in MATH_DELIMITERS:
        if trimmed.startswith(start):
            if len(trimmed) < len(start) + len(end) or not trimmed.endswith(end):
                return text, True
            latex = trimmed[len(start):-len(end)]
            delimited = True
            break
    if not delimited and MATH_DELIMITER.search(trimmed):
        return text, True

    braces = 0
    environments: list[str] = []
    for token in TEX_TOKENS.findall(latex):
        if token == "{":
            braces += 1
        elif token == "}":
            braces -= 1
            if braces < 0:
                return latex, True
        elif token.startswith(r"\begin{"):
            environments.append(token[7:-1])
        elif token.startswith(r"\end{"):
            if not environments or environments.pop() != token[5:-1]:
                return latex, True
    embedded_marker = delimited and bool(MATH_DELIMITER.search(latex))
    return latex, not latex.strip() or bool(braces or environments or embedded_marker)


def _is_mixed_math(text: str) -> bool:
    """An equation label can cover prose with inline math or a sequence of formulas."""
    trimmed = text.strip()
    spans = list(MATH_SPAN.finditer(trimmed))
    if not spans or (len(spans) == 1 and spans[0].span() == (0, len(trimmed))):
        return False
    end = 0
    for span in spans:
        if MATH_DELIMITER.search(trimmed, end, span.start()) or _math(span[0])[1]:
            return False
        end = span.end()
    return not MATH_DELIMITER.search(trimmed, end)


def _list_items(text: str) -> tuple[ListItem, ...]:
    items: list[ListItem] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = LIST_MARKER.match(line)
        if match:
            indent, marker, body = match.groups()
            items.append(ListItem(marker, body, len(indent.expandtabs(4))))
        elif items:
            item = items[-1]
            items[-1] = replace(item, text=item.text + "\n" + line.strip())
        else:
            items.append(ListItem(None, line.strip()))
    return tuple(items)


def _pipe_cells(line: str) -> list[str]:
    line = line.strip()
    bars = [match.start() for match in PIPE.finditer(line) if match.group() == "|"]
    if not bars:
        return []
    cells = [line[left + 1:right].strip()
             for left, right in zip([-1, *bars], [*bars, len(line)])]
    if bars[0] == 0:
        cells.pop(0)
    if bars[-1] == len(line) - 1 and cells:
        cells.pop()
    return cells


def _rule(cells: list[str]) -> bool:
    return bool(cells) and all(TABLE_RULE.fullmatch(cell) for cell in cells)


def _pipe_table(text: str) -> tuple[tuple[tuple[TableCell, ...], ...], bool]:
    lines = [line for line in text.splitlines() if line.strip()]
    width = len(_pipe_cells(lines[0])) if lines else 0
    rows = []
    malformed = len(lines) < 2 or not width
    has_header = len(lines) >= 2 and _rule(_pipe_cells(lines[1]))
    malformed |= not has_header
    for index, line in enumerate(lines):
        cells = _pipe_cells(line)
        malformed |= len(cells) != width
        if has_header and index == 1:
            continue
        if cells:
            header = index == 0 if has_header else None
            rows.append(tuple(TableCell(cell, header) for cell in cells))
    return tuple(rows), malformed


class _HTMLTable(HTMLParser):
    """Read table cells and spans while retaining malformed source in the caller."""

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[TableCell, ...]] = []
        self.row: list[TableCell] | None = None
        self.cell: list[str] | None = None
        self.cell_tag: str | None = None
        self.rowspan = self.colspan = 1
        self.table_open = False
        self.seen_table = False
        self.in_header = False
        self.malformed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.malformed |= self.seen_table
            self.table_open = self.seen_table = True
        elif tag == "thead":
            self.in_header = True
        elif tag == "tr":
            if not self.table_open or self.row is not None:
                self.malformed = True
                return
            self.row = []
        elif tag in {"td", "th"}:
            if self.row is None or self.cell is not None:
                self.malformed = True
                return
            self.cell, self.cell_tag = [], tag
            fields = dict(attrs)
            self.rowspan = self._span(fields.get("rowspan", "1"))
            self.colspan = self._span(fields.get("colspan", "1"))
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            if self.row is None or self.cell is None or tag != self.cell_tag:
                self.malformed = True
                return
            self.row.append(TableCell(
                "".join(self.cell).strip(), tag == "th" or self.in_header,
                self.rowspan, self.colspan,
            ))
            self.cell = self.cell_tag = None
        elif tag == "tr":
            if not self.row or self.cell is not None:
                self.malformed = True
            if self.row:
                self.rows.append(tuple(self.row))
            self.row = self.cell = self.cell_tag = None
        elif tag == "table":
            self.malformed |= not self.table_open or self.row is not None
            self.table_open = False
        elif tag == "thead":
            self.in_header = False

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def _span(self, value: str | None) -> int:
        if value is not None and re.fullmatch(r"[0-9]{1,4}", value) and 0 < int(value) <= 1000:
            return int(value)
        self.malformed = True
        return 1


def _html_has_gaps(rows: tuple[tuple[TableCell, ...], ...]) -> bool:
    pending: dict[int, int] = {}
    width = None
    for row in rows:
        covered = set(pending)
        column = 0
        for cell in row:
            while column in covered:
                column += 1
            for col in range(column, column + cell.colspan):
                if col in covered:
                    return True
                covered.add(col)
                if cell.rowspan > 1:
                    pending[col] = cell.rowspan
            column += cell.colspan
        width = len(covered) if width is None else width
        if len(covered) != width or max(covered, default=-1) != width - 1:
            return True
        pending = {col: span - 1 for col, span in pending.items() if span > 1}
    return bool(pending)


def _table(text: str) -> tuple[tuple[tuple[TableCell, ...], ...], bool]:
    if not HTML_TABLE.search(text):
        return _pipe_table(text)
    parser = _HTMLTable()
    try:
        parser.feed(text)
        parser.close()
    except AssertionError:
        # HTMLParser rejects some invalid declarations before it can close the table.
        parser.malformed = True
    rows = tuple(parser.rows)
    return rows, parser.malformed or parser.table_open or not rows or _html_has_gaps(rows)


def _build_block(
    block_id: str, kind: BlockKind, text: str, *, label: str | None = None,
    provider_type: str | None = None, order: int | None = None,
    bbox: Box | None = None, confidence: float | None = None, flags: tuple[str, ...] = (),
    heading_level: int | None = None,
) -> OCRBlock:
    items = _list_items(text) if kind == "list" else None
    rows = latex = None
    issues = list(flags)
    if not text.strip() and kind not in OPTIONALLY_EMPTY:
        issues.append("missing_text")
    if kind == "table":
        rows, malformed = _table(text)
        if malformed:
            issues.append("malformed_table")
    elif kind == "equation":
        if _is_mixed_math(text):
            # A paragraph keeps every span and surrounding word under the original OCR ID.
            kind = "paragraph"
        else:
            latex, malformed = _math(text)
            if malformed:
                issues.append("malformed_math")
    elif kind not in {"code", "figure"} and _has_unclosed_math(text):
        issues.append("malformed_math")
    return OCRBlock(
        id=block_id, kind=kind, text=text, provider_label=label, provider_type=provider_type,
        provider_order=order, bbox=bbox, confidence=confidence, flags=tuple(issues),
        heading_level=heading_level, items=items, rows=rows, latex=latex,
    )


def _has_unclosed_math(text: str) -> bool:
    return (
        len(re.findall(r"(?<!\\)\$\$", text)) % 2 != 0
        or text.count(r"\[") != text.count(r"\]")
        or text.count(r"\(") != text.count(r"\)")
    )


def _structured_block(block_id: str, raw: object) -> OCRBlock:
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        raise OCRSchemaError(f"Invalid structured OCR block {block_id}.")
    label, provider_type = raw.get("label"), raw.get("type")
    if label is not None and not isinstance(label, str):
        raise OCRSchemaError(f"Invalid layout label for {block_id}.")
    if provider_type is not None and not isinstance(provider_type, str):
        raise OCRSchemaError(f"Invalid layout type for {block_id}.")
    label_key = (label or "").strip().lower()
    type_key = (provider_type or "").strip().lower()
    if not (label_key or type_key):
        raise OCRSchemaError(f"Missing layout label and type for {block_id}.")

    kind = LABEL_KINDS.get(label_key) or TYPE_KINDS.get(type_key, "paragraph")
    order = raw.get("order")
    bbox = _box(raw.get("bbox_xyxy"))
    confidence = _number(raw.get("conf"))
    flags = []
    if order is not None and (type(order) is not int or order < 0):
        order = None
        flags.append("invalid_provider_order")
    if bbox is None and raw.get("bbox_xyxy") is not None:
        flags.append("invalid_bbox")
    if raw.get("conf") is not None and (confidence is None or not 0 <= confidence <= 1):
        confidence = None
        flags.append("invalid_confidence")
    level = HEADING_LEVELS.get(label_key, 1 if type_key == "title" else 2)
    return _build_block(
        block_id, kind, raw["text"], label=label, provider_type=provider_type, order=order,
        bbox=bbox, confidence=confidence, flags=tuple(flags),
        heading_level=level if kind == "heading" else None,
    )


def _markdown_kind(lines: list[str], index: int) -> BlockKind:
    line = lines[index].strip()
    if FENCE.match(line):
        return "code"
    if ATX_HEADING.match(lines[index]) or (
        index + 1 < len(lines) and SETEXT.fullmatch(lines[index + 1].strip())
        and not _pipe_cells(line) and not LIST_MARKER.match(line)
        and not line.startswith(("$", r"\[", r"\("))
    ):
        return "heading"
    if line.startswith(("$$", r"\[", r"\(")) or (
        line.startswith("$") and line.endswith("$") and len(line) > 2
    ):
        return "equation"
    if HTML_TABLE.match(line):
        return "table"
    cells = _pipe_cells(line)
    if cells and (
        (index + 1 < len(lines) and _pipe_cells(lines[index + 1]))
        or (line.startswith("|") and line.endswith("|") and len(cells) >= 2)
    ):
        return "table"
    if LIST_MARKER.match(lines[index]):
        return "list"
    if MD_IMAGE.fullmatch(line):
        return "figure"
    if HTML_CAPTION.fullmatch(line) or FIGURE_CAPTION.match(line):
        return "caption"
    return "paragraph"


def _markdown_blocks(markdown: str) -> Iterator[tuple[BlockKind, str, int | None]]:
    lines = markdown.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        start = index
        line = lines[start].rstrip("\r\n")
        if not line.strip():
            index += 1
            continue

        kind = _markdown_kind(lines, start)
        index += 1
        if kind == "heading":
            heading = ATX_HEADING.match(line)
            if heading:
                title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2]).strip()
                yield kind, title, len(heading[1])
            else:
                yield kind, line.strip(), 1 if lines[index].strip().startswith("=") else 2
                index += 1
            continue

        if kind == "code":
            fence = FENCE.match(line)[1]
            closing = re.compile(rf"^\s*{re.escape(fence[0])}{{{len(fence)},}}\s*$")
            while index < len(lines):
                candidate = lines[index]
                index += 1
                if closing.fullmatch(candidate):
                    break
        elif kind == "equation":
            opener, closer = next(pair for pair in MATH_DELIMITERS
                                  if line.lstrip().startswith(pair[0]))
            if (opener, closer) in DISPLAY_DELIMITERS and not (
                line.lstrip()[len(opener):].rstrip().endswith(closer)
            ):
                while index < len(lines) and lines[index].strip():
                    candidate = lines[index].rstrip()
                    if candidate.endswith(closer):
                        index += 1
                        break
                    if _markdown_kind(lines, index) != "paragraph":
                        break
                    index += 1
        elif kind == "table":
            if HTML_TABLE.match(line.lstrip()):
                closing = next((end for end in range(start, len(lines))
                                if HTML_TABLE_END.search(lines[end])), None)
                if closing is not None:
                    index = closing + 1
                else:
                    while index < len(lines) and lines[index].strip() and lines[index].lstrip().startswith("<"):
                        index += 1
            else:
                while index < len(lines) and _pipe_cells(lines[index]):
                    index += 1
        elif kind == "list":
            while index < len(lines):
                candidate = lines[index]
                if LIST_MARKER.match(candidate) or (
                    candidate.strip() and candidate.startswith((" ", "\t"))
                    and _markdown_kind(lines, index) == "paragraph"
                ):
                    index += 1
                else:
                    break
        elif kind == "paragraph":
            while (index < len(lines) and lines[index].strip()
                   and _markdown_kind(lines, index) == "paragraph"):
                index += 1

        text = "".join(lines[start:index]).rstrip("\r\n")
        if kind == "caption":
            caption = HTML_CAPTION.fullmatch(text.strip())
            if caption:
                text = caption[1]
        yield kind, text, None


def _provider_order(blocks: tuple[OCRBlock, ...]) -> tuple[OCRBlock, ...]:
    orders = [block.provider_order for block in blocks]
    if None in orders or len(set(orders)) != len(orders):
        return blocks
    return tuple(sorted(blocks, key=lambda block: block.provider_order or 0))


def _caption_gap(caption: Box, target: Box) -> float | None:
    """Match captions inside or close to an aligned figure or table."""
    left, top, right, bottom = caption
    target_left, target_top, target_right, target_bottom = target
    overlap = min(right, target_right) - max(left, target_left)
    if overlap < 0.65 * min(right - left, target_right - target_left):
        return None
    gap = max(0.0, top - target_bottom, target_top - bottom)
    return gap if gap <= 2 * (bottom - top) else None


def _link_captions(blocks: tuple[OCRBlock, ...]) -> tuple[OCRBlock, ...]:
    ordered = _provider_order(blocks)
    links: dict[str, str] = {}
    for index, block in enumerate(ordered):
        if block.kind != "caption":
            continue
        label = (block.provider_label or "").strip().lower()
        target_kinds = {"figure", "table"}
        if label == "table-caption" or re.match(r"^\s*(?:table|सारणी)\b", block.text, re.I):
            target_kinds = {"table"}
        elif label == "image-caption" or FIGURE_CAPTION.match(block.text):
            target_kinds = {"figure"}

        if block.bbox is not None:
            aligned = sorted(
                (gap, target.id)
                for target in ordered
                if target.kind in target_kinds and target.bbox is not None
                if (gap := _caption_gap(block.bbox, target.bbox)) is not None
            )
            if aligned:
                # Use a clear distance difference when more than one target could fit.
                height = block.bbox[3] - block.bbox[1]
                if len(aligned) == 1 or aligned[1][0] - aligned[0][0] > 0.5 * height:
                    links[block.id] = aligned[0][1]
                continue

        nearby = ordered[max(0, index - 1):index] + ordered[index + 1:index + 2]
        candidates = [
            neighbor.id for neighbor in nearby
            if neighbor.kind in target_kinds and (block.bbox is None or neighbor.bbox is None)
        ]
        if len(candidates) == 1:
            links[block.id] = candidates[0]
    return tuple(replace(block, caption_for=links[block.id]) if block.id in links else block
                 for block in blocks)


def parse_ocr_response(page_id: str, response: OCRResponse) -> tuple[OCRBlock, ...]:
    """Return blocks in raw array order, falling back to the source Markdown."""
    raw_blocks = response.raw.get("blocks")
    if "blocks" in response.raw and not isinstance(raw_blocks, list):
        raise OCRSchemaError("OCR response blocks must be a JSON array.")
    if raw_blocks:
        blocks = tuple(_structured_block(f"{page_id}-b{index:03d}", raw)
                       for index, raw in enumerate(raw_blocks, start=1))
    else:
        blocks = tuple(
            _build_block(f"{page_id}-b{index:03d}", kind, text, heading_level=level)
            for index, (kind, text, level) in enumerate(_markdown_blocks(response.markdown), start=1)
        )
    return _link_captions(blocks)


def ocr_page(
    page: Page, png: bytes, ocr: Callable[[str, bytes], OCRResponse], attempts: AttemptLog,
    *, image_path: str | None = None,
) -> OCRPage:
    """Call the shared adapter and parse its response for a page or a crop."""
    digest = sha256(png).hexdigest()
    first_attempt = len(attempts.attempts)
    record = OCRPage(page.page_id, page.input_index, "failed",
                     image_path=image_path, image_digest=digest)
    try:
        response = ocr(page.page_id, png)
    except ProviderError as error:
        failed_attempts = tuple(attempts.attempts[first_attempt:])
        raw = error.raw if isinstance(error, OCRResponseError) else None
        return replace(
            record, reason=str(error), raw_response=raw, attempts=failed_attempts,
            attempt_ids=tuple(attempt.id for attempt in failed_attempts),
        )

    linked_attempts = set(response.attempt_ids)
    record = replace(
        record, raw_response=response.raw, attempt_ids=response.attempt_ids,
        attempts=tuple(attempt for attempt in attempts.attempts if attempt.id in linked_attempts),
    )
    try:
        blocks = parse_ocr_response(page.page_id, response)
    except OCRSchemaError as error:
        return replace(record, reason=str(error))
    if not blocks:
        return replace(record, reason="OCR returned no blocks for this page.")

    has_issues = any(CONTENT_FLAGS.intersection(block.flags) for block in blocks)
    return replace(
        record, status="partial" if has_issues else "ok", blocks=blocks,
        raw_block_ids=tuple(block.id for block in blocks),
        ordered_block_ids=tuple(block.id for block in _provider_order(blocks)),
        reason="Some OCR blocks have missing or malformed content." if has_issues else None,
    )


def write_ocr_page(out: Path, record: OCRPage) -> None:
    (out / "source/pages" / f"{record.page_id}.json").write_text(
        json.dumps(asdict(record), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def extract_pages(
    pages: tuple[Page, ...], preprocessed: tuple[PreprocessedPage, ...], out: Path,
    ocr: Callable[[str, bytes], OCRResponse], attempts: AttemptLog,
    *, save_page: Callable[[str, Callable[[], OCRPage]], OCRPage] | None = None,
) -> tuple[OCRPage, ...]:
    """Consume normalized page records and persist every OCR outcome in input order."""
    directory = out / "source/pages"
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for page, image in zip(pages, preprocessed, strict=True):
        failed = OCRPage(page.page_id, page.input_index, "failed", image_path=image.normalized_path)
        if image.status != "ok" or image.normalized_path is None:
            record = replace(failed, reason=image.reason or "Normalized page is unavailable.")
        else:
            try:
                png = (out / image.normalized_path).read_bytes()
            except OSError:
                record = replace(failed, reason="Could not read the normalized PNG.")
            else:
                if sha256(png).hexdigest() != image.output_sha256:
                    record = replace(failed, reason="Normalized PNG digest differs from preprocessing.")
                else:
                    def read_page() -> OCRPage:
                        return ocr_page(page, png, ocr, attempts, image_path=image.normalized_path)

                    record = read_page() if save_page is None else save_page(page.page_id, read_page)
        write_ocr_page(out, record)
        records.append(record)
    return tuple(records)
