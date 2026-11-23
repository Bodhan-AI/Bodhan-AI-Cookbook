"""T2 number_unit: every digit/quantity in the source must survive in the translation.

Compares numbers as multisets after normalization (so "२५" == "25"). A number present in
the source but missing or changed in the translation is STRONG evidence with the exact digit
span. Extra numbers in the translation are flagged too (possible addition/mistranslation).
"""

from __future__ import annotations

from ..models import Evidence
from ..numbers import extract_numbers, multiset_diff
from .registry import CheckContext, register


@register("t2_number_unit", category="number_unit",
          description="number/unit preservation between source and translation")
def check(ctx: CheckContext) -> list[Evidence]:
    tr = ctx.translation
    if not tr or not ctx.source.strip():
        return []
    src_nums = extract_numbers(ctx.source, "en")
    tgt_nums = extract_numbers(tr, ctx.lang)
    if not src_nums and not tgt_nums:
        return []
    missing, extra = multiset_diff(src_nums, tgt_nums)
    out: list[Evidence] = []
    for t in missing:
        out.append(Evidence(
            check="t2_number_unit", category="number_unit", strength="strong", noisy=False,
            score=1.0, severity_hint="major", source_span=t.span,
            details={"issue": "missing_or_changed", "value": t.value, "unit": t.unit,
                     "raw": t.raw},
        ))
    for t in extra:
        out.append(Evidence(
            check="t2_number_unit", category="number_unit", strength="medium", noisy=False,
            score=0.8, severity_hint="major", target_span=t.span,
            details={"issue": "extra_in_translation", "value": t.value, "unit": t.unit,
                     "raw": t.raw},
        ))
    return out
