"""T4 length_ratio: target/source length ratio, z-scored against per-language stats when
calibration has learned them, else against wide generic bounds.

WEAK evidence only -- it supports omission (too short) or addition (too long) but never flags
on its own, because length varies a lot by script and content.
"""

from __future__ import annotations

from ..models import Evidence
from ..normalize import norm_for_compare
from .registry import CheckContext, register

# generic fallback bounds on target/source character ratio (script-agnostic, wide)
_GENERIC_LOW = 0.45
_GENERIC_HIGH = 2.6


@register("t4_length_ratio", category="omission",
          description="target/source length ratio as weak omission/addition support")
def check(ctx: CheckContext) -> list[Evidence]:
    tr = ctx.translation
    if not tr or not ctx.source.strip():
        return []
    s_len = len(norm_for_compare(ctx.source, "en"))
    t_len = len(norm_for_compare(tr, ctx.lang))
    if s_len == 0:
        return []
    ratio = t_len / s_len

    mean = ctx.thresholds.per_language.get(ctx.lang, {}).get("length_ratio_mean")
    std = ctx.thresholds.per_language.get(ctx.lang, {}).get("length_ratio_std")
    z_abs = ctx.thresholds.get("length_ratio_z_abs", ctx.lang)

    too_short = too_long = False
    z = None
    if mean and std and std > 1e-6:
        z = (ratio - mean) / std
        too_short = z < -z_abs
        too_long = z > z_abs
    else:
        too_short = ratio < _GENERIC_LOW
        too_long = ratio > _GENERIC_HIGH

    if not (too_short or too_long):
        return []
    category = "omission" if too_short else "addition"
    return [Evidence(
        check="t4_length_ratio", category=category, strength="weak", noisy=True,
        score=abs(z) if z is not None else ratio, severity_hint="minor",
        details={"ratio": round(ratio, 3), "z": round(z, 2) if z is not None else None,
                 "src_len": s_len, "tgt_len": t_len},
    )]
