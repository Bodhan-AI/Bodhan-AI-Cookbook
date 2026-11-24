"""Markdown and LaTeX structural integrity.

Translation models routinely flatten structure: table pipes vanish, headings
lose their hashes, LaTeX delimiters get "translated" into prose. All of it is
deterministic to detect and trivial to explain to a model team.
"""

from __future__ import annotations

import re

from ..spans import span_preserving
from .base import Flag, ItemContext

HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s")
TABLE_ROW = re.compile(r"(?m)^\s*\|.*\|\s*$")
CODE_FENCE = re.compile(r"```")
INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)[^$\n]+\$(?!\$)")
DISPLAY_MATH = re.compile(r"\$\$.+?\$\$", re.DOTALL)
LATEX_CMD = re.compile(r"\\[a-zA-Z]{2,}")
LIST_ITEM = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s")


def _counts(text: str) -> dict[str, int]:
    return {
        "headings": len(HEADING.findall(text)),
        "table_rows": len(TABLE_ROW.findall(text)),
        "code_fences": len(CODE_FENCE.findall(text)),
        "inline_math": len(INLINE_MATH.findall(text)),
        "display_math": len(DISPLAY_MATH.findall(text)),
        "latex_cmds": len(set(LATEX_CMD.findall(text))),
        "list_items": len(LIST_ITEM.findall(text)),
    }


def check_format(ctx: ItemContext) -> list[Flag]:
    if not ctx.translation:
        return []
    src, tgt = _counts(ctx.source), _counts(ctx.translation)
    flags: list[Flag] = []

    # Structural elements that must survive one-for-one.
    for key, label, severity in (
        ("table_rows", "table rows", "major"),
        ("display_math", "display-math blocks", "major"),
        ("headings", "headings", "minor"),
        ("code_fences", "code fences", "major"),
    ):
        if src[key] and tgt[key] < src[key]:
            flags.append(Flag(
                category="format", severity=severity,
                span=_first_missing_span(ctx.translation, key),
                evidence=f"{label}: {src[key]} in source, {tgt[key]} in translation",
                check=f"format.{key}", confidence=0.9,
            ))

    # LaTeX commands that disappeared entirely.
    src_cmds = set(LATEX_CMD.findall(ctx.source))
    tgt_cmds = set(LATEX_CMD.findall(ctx.translation))
    lost = src_cmds - tgt_cmds
    if lost:
        flags.append(Flag(
            category="format", severity="major", span=None,
            evidence=f"LaTeX commands lost: {', '.join(sorted(lost))[:120]}",
            check="format.latex_lost", confidence=0.85,
        ))

    # Inline math should not silently double or vanish.
    if src["inline_math"] and tgt["inline_math"] == 0 and src["display_math"] == 0:
        flags.append(Flag(
            category="format", severity="minor", span=None,
            evidence=f"inline math: {src['inline_math']} in source, none in translation",
            check="format.inline_math", confidence=0.7,
        ))
    return flags


def _first_missing_span(text: str, kind: str) -> tuple[int, int] | None:
    """Point at where the structure stops matching, if we can find it."""
    pattern = {"table_rows": TABLE_ROW, "headings": HEADING,
               "code_fences": CODE_FENCE, "display_math": DISPLAY_MATH}.get(kind)
    if pattern is None:
        return None
    view = span_preserving(text)
    matches = list(pattern.finditer(view))
    if not matches:
        return (0, min(len(text), 40))
    m = matches[-1]
    return (m.start(), m.end())
