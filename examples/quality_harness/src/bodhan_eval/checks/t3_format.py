"""T3 format: Markdown and LaTeX structural parity between source and translation.

Translators (and seeded errors) break tables, code fences, math delimiters and headings.
These are deterministic and script-independent, so mismatches are STRONG evidence. We never
translate-compare inside LaTeX/code; we only check that the markup structure survived and
that math bodies are byte-identical (math must not be translated).
"""

from __future__ import annotations

import re

from ..models import Evidence
from .registry import CheckContext, register

_FENCE = re.compile(r"```")
_INLINE_CODE = re.compile(r"`")
_HEADING = re.compile(r"^(#{1,6})\s", re.MULTILINE)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s", re.MULTILINE)
_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)([^$]+?)\$(?!\$)")


def _features(text: str) -> dict:
    return {
        "fences": len(_FENCE.findall(text)),
        "inline_code_ticks": len(_INLINE_CODE.findall(text)) - 2 * len(_FENCE.findall(text)) // 2,
        "headings": len(_HEADING.findall(text)),
        "list_items": len(_LIST_ITEM.findall(text)),
        "links": len(_LINK.findall(text)),
        "table_rows": len(_TABLE_ROW.findall(text)),
        "table_seps": len(_TABLE_SEP.findall(text)),
        "dollar": text.count("$"),
        "display_math": len(_DISPLAY_MATH.findall(text)),
        "latex_begin": len(re.findall(r"\\begin\{", text)),
        "latex_end": len(re.findall(r"\\end\{", text)),
        "open_brace": text.count("{"),
        "close_brace": text.count("}"),
    }


def _math_bodies(text: str) -> list[str]:
    bodies = [m.group(1).strip() for m in _DISPLAY_MATH.finditer(text)]
    bodies += [m.group(1).strip() for m in _INLINE_MATH.finditer(text)]
    return sorted(bodies)


@register("t3_format", category="format",
          description="Markdown + LaTeX structural parity; math bodies unchanged")
def check(ctx: CheckContext) -> list[Evidence]:
    tr = ctx.translation
    if not tr or not ctx.source.strip():
        return []
    src, dst = ctx.source, tr
    sf, df = _features(src), _features(dst)
    out: list[Evidence] = []

    # internal balance problems in the translation (independent of source)
    if df["open_brace"] != df["close_brace"] or df["latex_begin"] != df["latex_end"] or df["dollar"] % 2:
        out.append(Evidence(
            check="t3_format", category="format", strength="strong", noisy=False, score=1.0,
            severity_hint="major", target_span=(0, len(tr)),
            details={"issue": "unbalanced_markup",
                     "dollar": df["dollar"], "braces": (df["open_brace"], df["close_brace"]),
                     "latex_env": (df["latex_begin"], df["latex_end"])},
        ))

    # structural parity vs source
    for feat in ("fences", "headings", "list_items", "links", "table_rows", "table_seps", "display_math"):
        if sf[feat] != df[feat]:
            out.append(Evidence(
                check="t3_format", category="format", strength="strong", noisy=False,
                score=1.0, severity_hint="major", target_span=(0, len(tr)),
                details={"issue": f"{feat}_mismatch", "source": sf[feat], "translation": df[feat]},
            ))

    # math bodies must be identical (math is never translated)
    sb, db = _math_bodies(src), _math_bodies(dst)
    if sb and sb != db:
        out.append(Evidence(
            check="t3_format", category="format", strength="strong", noisy=False, score=1.0,
            severity_hint="major", target_span=(0, len(tr)),
            details={"issue": "math_body_changed", "source": sb, "translation": db},
        ))
    return out
