"""Number and unit preservation.

The highest-precision check in the harness. A changed digit in a dosage, a date
or a percentage is a real, damaging error and it localizes exactly — the wrong
number is right there in the output.

False-alarm control: single digits are ignored unless they carry a unit,
because translators legitimately spell small numbers out ("3" -> "three").
"""

from __future__ import annotations

import re

from ..normalize import normalize
from ..spans import find_all, snippet
from .base import Flag, ItemContext

NUM = re.compile(r"\d+(?:[.,]\d+)*")
# A number is "significant" if it is multi-digit or immediately followed by a
# unit/percent sign, where a silent change is almost always a real error.
UNIT_AFTER = re.compile(
    r"^\s*(?:%|°\s*[CF]|km|kg|mg|ml|cm|mm|GB|MB|ms|s\b|min|hr|hours?|days?|years?|"
    r"rupees?|rs\.?|inr|₹|\$|€)",
    re.IGNORECASE,
)


def _numbers(text: str) -> list[tuple[str, int, int, bool]]:
    """(value, start, end, significant) for each numeric token."""
    out = []
    for val, start, end in find_all(NUM, text):
        significant = len(re.sub(r"\D", "", val)) >= 2 or bool(UNIT_AFTER.match(text[end:]))
        out.append((val.replace(",", ""), start, end, significant))
    return out


def check_numbers(ctx: ItemContext) -> list[Flag]:
    if not ctx.translation:
        return []
    src = _numbers(ctx.source)
    tgt = _numbers(ctx.translation)
    flags: list[Flag] = []

    src_vals = {v for v, _, _, _ in src}
    tgt_vals = {v for v, _, _, _ in tgt}

    # A number present in the output but absent from the source: the classic
    # "digit changed" seed, and it localizes exactly.
    for val, start, end, significant in tgt:
        if val not in src_vals and significant:
            flags.append(Flag(
                category="number", severity="critical", span=(start, end),
                evidence=f"'{val}' appears in the translation but not in the source "
                         f"(context: {snippet(ctx.translation, (start, end), 30)})",
                check="numbers.added_or_changed", confidence=0.9,
            ))

    # A significant number dropped from the output: omission.
    for val, start, end, significant in src:
        if val not in tgt_vals and significant:
            # localize at the nearest number in the output, else the whole text
            span = None
            if tgt:
                nearest = min(tgt, key=lambda t: abs(t[1] - start))
                span = (nearest[1], nearest[2])
            else:
                span = (0, min(len(ctx.translation), 40))
            flags.append(Flag(
                category="number", severity="critical", span=span,
                evidence=f"source number '{val}' is missing from the translation "
                         f"(span points at the nearest output number; omission cannot "
                         f"be localized exactly)",
                check="numbers.missing", confidence=0.75,
            ))
    return flags


def check_units(ctx: ItemContext) -> list[Flag]:
    """Units and currency symbols that survive into the output."""
    if not ctx.translation:
        return []
    flags: list[Flag] = []
    for unit in re.findall(r"°\s*[CF]|%|₹|\$|€|km|kg|mg|ml", ctx.source):
        u = normalize(unit).replace(" ", "")
        if u and u not in normalize(ctx.translation).replace(" ", ""):
            flags.append(Flag(
                category="number", severity="major", span=None,
                evidence=f"unit '{unit}' from the source is missing in the translation",
                check="numbers.unit_lost", confidence=0.6,
            ))
    return flags
