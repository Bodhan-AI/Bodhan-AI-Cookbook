"""Cleanup rules that need page context (plan.v1.md rules 3, 8 and 10)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PIL import Image

from bodhan_lesson import config
from bodhan_lesson.models import Block, DroppedBlock, Page

_TITLE_TYPES = frozenset({"Title", "SectionHeader"})
FigureDescriber = Callable[[Path, Block], str]


# ---- Rule 3: running headers / footers --------------------------------------
def _header_key(text: str) -> str:
    """Normalise so "LINES AND ANGLES 97" and "Lines and angles 98" compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", text)).strip().lower()


def find_running_headers(pages: list[Page]) -> set[str]:
    """Title texts that repeat on >= 2 pages while sitting in the top band of the page."""
    pages_by_key: dict[str, set[int]] = defaultdict(set)
    for page in pages:
        for block in page.blocks:
            in_top_band = block.y_min < config.RUNNING_HEADER_BAND * page.height
            if block.type in _TITLE_TYPES and in_top_band and block.text.strip():
                pages_by_key[_header_key(block.text)].add(page.number)
    return {k for k, seen in pages_by_key.items() if k and len(seen) >= config.RUNNING_HEADER_MIN_PAGES}


def drop_furniture(pages: list[Page], running_headers: set[str]) -> None:
    """Move page furniture from `page.blocks` to `page.dropped`.

    A repeated title in the top band of the document's first page is kept: there it is
    the real chapter heading, not a running header.
    """
    first_page = pages[0].number if pages else None
    for page in pages:
        kept: list[Block] = []
        for block in page.blocks:
            reason = _furniture_reason(block, page, running_headers, keep_titles=page.number == first_page)
            if reason:
                page.dropped.append(DroppedBlock(block, reason))
            else:
                kept.append(block)
        page.blocks = kept


def _furniture_reason(block: Block, page: Page, headers: set[str], *, keep_titles: bool) -> str | None:
    if block.type in config.FURNITURE_TYPES:
        return f"rule3_furniture_type:{block.type}"
    if block.label in config.FURNITURE_LABELS:
        return f"rule3_furniture_label:{block.label}"
    key = _header_key(block.text)
    in_top_band = block.y_min < config.RUNNING_HEADER_BAND * page.height
    if block.type in _TITLE_TYPES and in_top_band and key in headers and not keep_titles:
        return "rule3_running_header"
    return None


# ---- Rule 8: figures ---------------------------------------------------------
def is_figure(block: Block) -> bool:
    return block.type in config.FIGURE_TYPES or block.label in config.FIGURE_LABELS


def crop_figures(page: Page, figure_dir: Path, describer: FigureDescriber | None = None) -> None:
    """Crop each figure block from the page image and optionally describe it.

    Describing is injected (`describer`) so this stage stays deterministic and offline;
    without one, `figure_description` stays empty for a later LLM stage to fill.
    """
    figures = [b for b in page.blocks if is_figure(b)]
    if not figures:
        return
    figure_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(page.image_path) as image:
        for index, block in enumerate(page.blocks):
            if not is_figure(block):
                continue
            pad = config.FIGURE_CROP_PADDING_PX
            x0, y0, x1, y1 = block.bbox_xyxy
            box = (max(0, int(x0) - pad), max(0, int(y0) - pad), min(image.width, int(x1) + pad), min(image.height, int(y1) + pad))
            path = figure_dir / f"{block.id.replace('.', '_')}.png"
            image.crop(box).save(path)
            description = describer(path, block) if describer else ""
            page.blocks[index] = replace(
                block,
                figure_path=str(path),
                figure_description=description,
                rules_applied=[*block.rules_applied, "rule8_figure_crop"],
            )


# ---- Rule 10: page-break joining --------------------------------------------
_TERMINAL_PUNCTUATION = (".", "?", "!")


def _continues_on_next_page(block: Block) -> bool:
    """A Text block that does not end a sentence (a trailing ':' also continues)."""
    text = block.text.rstrip()
    return block.type == "Text" and bool(text) and not text.endswith(_TERMINAL_PUNCTUATION)


def join_page_breaks(pages: list[Page]) -> int:
    """Merge a sentence split across a page break into the earlier page's block.

    Returns the number of joins performed.
    """
    joins = 0
    for current, following in zip(pages, pages[1:]):
        if not current.blocks or not following.blocks:
            continue
        last, first = current.blocks[-1], following.blocks[0]
        if _continues_on_next_page(last) and first.type == "Text":
            current.blocks[-1] = replace(
                last,
                text=f"{last.text.rstrip()} {first.text.lstrip()}",
                rules_applied=[*last.rules_applied, f"rule10_joined:{first.id}"],
            )
            following.blocks = following.blocks[1:]
            joins += 1
    return joins
