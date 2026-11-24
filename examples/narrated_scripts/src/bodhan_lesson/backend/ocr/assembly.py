"""Cross-page decisions and source chapter assembly for ordered OCR records."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Literal

from ..models import (
    BlockKind, BoundaryDecision, ChapterEntry, OCRBlock, OCRPage, SourceBlock, SourceChapter,
    SourceSection, TableCell, UnavailableContent,
)
from .extraction import LIST_MARKER

Edge = Literal["top", "bottom"]
FURNITURE_KINDS: dict[Edge, BlockKind] = {"top": "header", "bottom": "footer"}
SENTENCE_END = ".!?…。！？।॥:;"
CLOSING_MARKS = "\"'”’»)]}"
# These Hindi conjunctions and postpositions can leave a phrase unfinished.
HINDI_CONTINUATION = {"और", "या", "कि", "का", "की", "के", "से", "को", "में", "पर"}


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _page_number(block: OCRBlock) -> int | None:
    text = block.text.strip()
    if block.kind in {"paragraph", "page_number", "header", "footer"} and re.fullmatch(r"\d{1,9}", text):
        return int(text)
    return None


def _edge_candidates(blocks: tuple[OCRBlock, ...], edge: Edge) -> Iterator[OCRBlock]:
    """Look past a folio or tagged furniture, stopping at the first ordinary block."""
    ordered = blocks if edge == "top" else reversed(blocks)
    for block in ordered:
        if "quarantined" in block.flags:
            break
        yield block
        if block.kind != FURNITURE_KINDS[edge] and _page_number(block) is None:
            break


def _exclude_furniture(
    pages: Sequence[OCRPage], ordered: Sequence[tuple[OCRBlock, ...]],
) -> list[BoundaryDecision]:
    recurring: dict[tuple[Edge, str], set[str]] = defaultdict(set)
    candidates: dict[str, list[tuple[Edge, OCRBlock, str]]] = defaultdict(list)
    for page, blocks in zip(pages, ordered):
        for edge, tagged_kind in FURNITURE_KINDS.items():
            tagged_furniture_seen = False
            for block in _edge_candidates(blocks, edge):
                if block.kind not in {tagged_kind, "paragraph"} or _page_number(block) is not None:
                    continue
                if block.kind == "paragraph" and (tagged_furniture_seen or len(blocks) == 1):
                    continue
                tagged_furniture_seen |= block.kind == tagged_kind
                normalized = _normalize(block.text)
                if normalized:
                    recurring[edge, normalized].add(page.page_id)
                    candidates[page.page_id].append((edge, block, normalized))

    decisions = []
    excluded: set[str] = set()
    folios: dict[tuple[Edge, int], tuple[OCRBlock, int]] = {}
    for page, blocks in zip(pages, ordered):
        for edge, block, normalized in candidates[page.page_id]:
            if block.id not in excluded and len(recurring[edge, normalized]) >= 2:
                decisions.append(BoundaryDecision(
                    "exclude", (page.page_id,), (block.id,),
                    f"Matching normalized text recurs at the {edge} edge of multiple pages.",
                ))
                excluded.add(block.id)
        remaining = tuple(block for block in blocks if block.id not in excluded)
        for edge in FURNITURE_KINDS:
            numbers = [
                (block, number)
                for block in _edge_candidates(remaining, edge)
                if (number := _page_number(block)) is not None
            ]
            if len(numbers) == 1:
                block, number = numbers[0]
                if len(remaining) > 1 or block.kind == "page_number":
                    folios[edge, page.input_index] = block, number

    page_ids = {page.input_index: page.page_id for page in pages}
    for (edge, index), (block, number) in folios.items():
        if block.id in excluded:
            continue
        if block.kind == "page_number":
            reason = f"OCR identifies the bare number at the {edge} edge as pagination."
        elif any(
            (neighbor := folios.get((edge, index + step))) is not None
            and neighbor[1] == number + step
            for step in (-1, 1)
        ):
            reason = f"The bare number increments across consecutive {edge} edges."
        else:
            continue
        decisions.append(BoundaryDecision("exclude", (page_ids[index],), (block.id,), reason))
        excluded.add(block.id)
    return decisions


def _prose_continues(left: str, right: str) -> bool:
    left = left.rstrip()
    right = right.lstrip()
    if not left or not right or left.endswith(("$$", r"\]")) or LIST_MARKER.match(right):
        return False
    ending = left.rstrip(CLOSING_MARKS).rstrip()
    if not right[0].isalpha() or (ending and ending[-1] in SENTENCE_END):
        return False
    # A lowercase start supports an unpunctuated split. For uncased scripts
    # require a stronger cue, such as a trailing comma or a stranded connector.
    return (
        right[0].islower()
        or left.endswith((",", "،", "("))
        or left.split()[-1] in HINDI_CONTINUATION
    )


def _table_width(rows: tuple[tuple[TableCell, ...], ...]) -> int | None:
    # Keep tables with row-spanning cells separate. Their grids require additional
    # evidence to tell whether a span continues across the page break.
    if any(not row or any(cell.rowspan != 1 or cell.colspan < 1 for cell in row) for row in rows):
        return None
    widths = {sum(cell.colspan for cell in row) for row in rows}
    return next(iter(widths)) if len(widths) == 1 else None


def _repeated_table_header(left: OCRBlock, right: OCRBlock) -> bool:
    if not left.rows or not right.rows or len(right.rows) < 2:
        return False
    width = _table_width(left.rows)
    if width is None or width != _table_width(right.rows):
        return False
    first, second = left.rows[0], right.rows[0]
    if not any(cell.is_header is True for cell in (*first, *second)):
        return False
    if any(all(cell.is_header is False for cell in row) for row in (first, second)):
        return False
    first_cells = tuple((_normalize(cell.text), cell.colspan) for cell in first)
    second_cells = tuple((_normalize(cell.text), cell.colspan) for cell in second)
    return any(text for text, _ in first_cells) and first_cells == second_cells


def _decide_pair(
    pages: tuple[OCRPage, OCRPage], left: OCRBlock | None, right: OCRBlock | None,
) -> BoundaryDecision:
    first, second = pages
    page_ids = (first.page_id, second.page_id)
    block_ids = tuple(block.id for block in (left, right) if block is not None)
    if second.input_index != first.input_index + 1 or any(page.status == "failed" for page in pages):
        reason = "A missing or failed page prevents a join."
    elif left is None or right is None:
        reason = "A page has no remaining edge block."
    elif "quarantined" in left.flags or "quarantined" in right.flags:
        reason = "A quarantined edge block prevents a join."
    elif left.kind == right.kind == "paragraph" and _prose_continues(left.text, right.text):
        return BoundaryDecision("join_prose", page_ids, block_ids,
                                "Unfinished prose has a continuation cue across the page break.")
    elif left.kind == right.kind == "table" and _repeated_table_header(left, right):
        return BoundaryDecision("join_table", page_ids, block_ids,
                                "Table column counts and the repeated header agree.",
                                drop_repeated_header=True)
    else:
        reason = "The edge blocks lack compatible continuation evidence."
    return BoundaryDecision("keep", page_ids, block_ids, reason)


def _ordered_blocks(page: OCRPage) -> tuple[OCRBlock, ...]:
    if page.status == "failed":
        return ()
    by_id = {block.id: block for block in page.blocks}
    return tuple(by_id[block_id] for block_id in page.ordered_block_ids)


def decide_boundaries(pages: Sequence[OCRPage]) -> tuple[BoundaryDecision, ...]:
    """Classify page furniture, then decide each adjacent pair in input order."""
    pages = sorted(pages, key=lambda page: page.input_index)
    ordered = [_ordered_blocks(page) for page in pages]

    decisions = _exclude_furniture(pages, ordered)
    excluded = {decision.block_ids[0] for decision in decisions}
    # Keep quarantined blocks in this sequence so missing text at an edge cannot
    # make blocks on either side appear adjacent.
    remaining = [tuple(block for block in blocks if block.id not in excluded) for blocks in ordered]
    for first, second, left, right in zip(pages, pages[1:], remaining, remaining[1:]):
        decisions.append(_decide_pair((first, second), left[-1] if left else None,
                                      right[0] if right else None))
    return tuple(decisions)


def _chapter_entries(
    pages: Sequence[OCRPage], decisions: Sequence[BoundaryDecision],
) -> list[ChapterEntry]:
    excluded: set[str] = set()
    joins: dict[str, BoundaryDecision] = {}
    for decision in decisions:
        if decision.action == "exclude":
            excluded.update(decision.block_ids)
        elif decision.action in {"join_prose", "join_table"}:
            _, right_id = decision.block_ids
            if right_id in joins and joins[right_id] != decision:
                raise ValueError(f"Conflicting boundary joins for {right_id}.")
            joins[right_id] = decision

    entries: list[ChapterEntry] = []
    next_index = 1
    for page in sorted(pages, key=lambda page: page.input_index):
        for missing in range(next_index, page.input_index):
            page_id = f"p{missing:03d}"
            entries.append(UnavailableContent(page_id, page_id, "The page record is missing."))
        next_index = page.input_index + 1
        if page.status == "failed":
            entries.append(UnavailableContent(page.page_id, page.page_id, page.reason or "OCR failed."))
            continue

        for block in _ordered_blocks(page):
            if block.id in excluded:
                continue
            if "quarantined" in block.flags:
                entries.append(UnavailableContent(
                    block.id, page.page_id, "OCR block is quarantined.", (block.id,), block.flags,
                    caption_for=block.caption_for,
                ))
                continue

            entry = SourceBlock(
                block.id, block.kind, (block.id,),
                text=block.text if block.kind not in {"table", "list", "equation"} else "",
                heading_level=block.heading_level,
                items=block.items, rows=block.rows, latex=block.latex, caption_for=block.caption_for,
            )
            decision = joins.pop(block.id, None)
            if decision is None:
                entries.append(entry)
                continue

            previous = entries[-1] if entries else None
            expected_kind = "paragraph" if decision.action == "join_prose" else "table"
            if (not isinstance(previous, SourceBlock)
                    or previous.source_block_ids[-1] != decision.block_ids[0]
                    or previous.kind != expected_kind or entry.kind != expected_kind):
                raise ValueError(
                    f"Boundary join does not match adjacent {expected_kind} blocks for {block.id}."
                )
            source_ids = (*previous.source_block_ids, block.id)
            if decision.action == "join_prose":
                text = previous.text.rstrip() + " " + entry.text.lstrip()
                entries[-1] = replace(previous, text=text, source_block_ids=source_ids)
            else:
                if not previous.rows or not entry.rows:
                    raise ValueError(f"Boundary join is missing table rows for {block.id}.")
                rows = previous.rows
                if decision.drop_repeated_header:
                    # The repeated row can supply header evidence missing from the first fragment.
                    header = tuple(
                        replace(cell, is_header=repeated.is_header) if cell.is_header is None else cell
                        for cell, repeated in zip(rows[0], entry.rows[0], strict=True)
                    )
                    rows = (header, *rows[1:], *entry.rows[1:])
                else:
                    rows = (*rows, *entry.rows)
                entries[-1] = replace(previous, rows=rows, source_block_ids=source_ids)

    if joins:
        raise ValueError(f"Boundary join references an unavailable block {next(iter(joins))}.")
    return entries


def _adjacent_captions(blocks: list[ChapterEntry]) -> tuple[ChapterEntry, ...]:
    """Place figure captions after their figure without crossing an unrelated gap."""
    positions: dict[str, tuple[int, int, BlockKind]] = {}
    region = 0
    for index, block in enumerate(blocks):
        if isinstance(block, UnavailableContent) and block.caption_for is None:
            region += 1
        elif block.kind in {"figure", "table"}:
            positions[block.id] = (index, region, block.kind)

    before: dict[str, list[ChapterEntry]] = defaultdict(list)
    after: dict[str, list[ChapterEntry]] = defaultdict(list)
    remaining: list[ChapterEntry] = []
    region = 0
    for index, block in enumerate(blocks):
        if isinstance(block, UnavailableContent) and block.caption_for is None:
            region += 1
        elif block.caption_for is not None and block.caption_for in positions:
            target_index, target_region, target_kind = positions[block.caption_for]
            if target_region == region:
                captions = before if target_kind == "table" and index < target_index else after
                captions[block.caption_for].append(block)
                continue
        remaining.append(block)

    ordered = []
    for block in remaining:
        ordered.extend(before.get(block.id, ()))
        ordered.append(block)
        ordered.extend(after.get(block.id, ()))
    return tuple(ordered)


def build_chapter(
    pages: Sequence[OCRPage], decisions: Sequence[BoundaryDecision], source_lang: str | None,
) -> SourceChapter:
    """Apply boundary actions and group lesson blocks and gaps under their headings."""
    entries = _chapter_entries(pages, decisions)
    merged_ids = {
        source_id: entry.id
        for entry in entries if isinstance(entry, SourceBlock)
        for source_id in entry.source_block_ids if source_id != entry.id
    }
    sections: list[SourceSection] = []
    ancestors: list[tuple[int, str]] = []
    section_blocks: list[ChapterEntry] = []
    section_id = "s001"
    level = heading_id = parent_id = None
    for entry in entries:
        if isinstance(entry, SourceBlock) and entry.kind == "heading":
            if section_blocks:
                sections.append(SourceSection(section_id, level, heading_id, parent_id,
                                              _adjacent_captions(section_blocks)))
                section_blocks = []
            section_id = f"s{len(sections) + 1:03d}"
            heading_id = entry.id
            level = max(1, min(6, entry.heading_level or 1))
            while ancestors and ancestors[-1][0] >= level:
                ancestors.pop()
            parent_id = ancestors[-1][1] if ancestors else None
            ancestors.append((level, section_id))
        elif entry.caption_for is not None and entry.caption_for in merged_ids:
            entry = replace(entry, caption_for=merged_ids[entry.caption_for])
        section_blocks.append(entry)

    if section_blocks:
        sections.append(SourceSection(section_id, level, heading_id, parent_id,
                                      _adjacent_captions(section_blocks)))
    return SourceChapter(source_lang, tuple(sections))
