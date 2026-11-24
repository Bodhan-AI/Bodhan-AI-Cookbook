"""Stage 3: deterministic cleanup of OCR blocks (the 11 rules of plan.v1.md)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from bodhan_lesson.models import Block, Page
from bodhan_lesson.stages.cleanup import block_rules as br
from bodhan_lesson.stages.cleanup import page_rules as pr


def _clean_block(block: Block, vocab: set[str]) -> Block:
    """Per-block rules in dependency order (type fixes first, flagging last)."""
    block = br.apply_reclassify_equation(block)  # 11
    block = br.apply_hyphenation(block, vocab)  # 6
    block = br.apply_whitespace_and_labels(block)  # 7
    block = br.apply_table_detection(block)  # 7b
    block = br.apply_example_number(block)  # 4
    block = br.apply_decimal_cdot(block)  # 1
    block = br.apply_angle_normalisation(block)  # 9
    return br.apply_doubtful_flag(block)  # 5


def cleanup_pages(
    pages: list[Page],
    figure_dir: Path,
    describer: pr.FigureDescriber | None = None,
) -> dict[str, object]:
    """Clean `pages` in place and return a summary of what each rule did."""
    pr.drop_furniture(pages, pr.find_running_headers(pages))  # 3
    for page in pages:
        vocab = br.build_vocab(page.blocks)
        page.blocks = [_clean_block(block, vocab) for block in page.blocks]
        pr.crop_figures(page, figure_dir, describer)  # 8
    joins = pr.join_page_breaks(pages)  # 10
    return _summarise(pages, joins)


def _summarise(pages: list[Page], joins: int) -> dict[str, object]:
    hits: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    for page in pages:
        for block in page.blocks:
            hits.update(rule.split(":")[0] for rule in block.rules_applied)
        dropped.update(d.reason for d in page.dropped)
    return {
        "pages": len(pages),
        "blocks_kept": sum(len(p.blocks) for p in pages),
        "blocks_dropped": sum(len(p.dropped) for p in pages),
        "doubtful_blocks": sum(b.doubtful for p in pages for b in p.blocks),
        "page_break_joins": joins,
        "rule_hits": dict(sorted(hits.items())),
        "dropped_by_reason": dict(sorted(dropped.items())),
    }
