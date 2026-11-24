"""Length ratio against a robust per-language baseline computed from the dataset itself.

No fixed per-language constants: the baseline (median and MAD of log(len(translation)/len(source)))
is estimated from whatever dataset is being scored, so an unseen language works unchanged.
This is a weak signal; it can raise suspicion but never flags on its own.
"""
from __future__ import annotations

import math
import statistics

from .base import Check, Context, Signal, register


def letters(text: str) -> int:
    return sum(ch.isalpha() or ch.isdigit() for ch in text)


def log_ratio(source: str, translation: str) -> float | None:
    a, b = letters(source), letters(translation)
    if a < 20 or b == 0:
        return None
    return math.log(b / a)


def baselines(pairs: list[tuple[str, str, str]]) -> dict:
    """pairs: (lang, source, translation) -> {lang: (median, robust_sd)} plus "_all"."""
    by: dict[str, list[float]] = {}
    for lang, s, t in pairs:
        r = log_ratio(s, t)
        if r is not None:
            by.setdefault(lang, []).append(r)
            by.setdefault("_all", []).append(r)
    out = {}
    for k, v in by.items():
        if len(v) >= 3:
            med = statistics.median(v)
            mad = statistics.median(abs(x - med) for x in v)
            out[k] = (med, max(1.4826 * mad, 0.08), len(v))
    return out


@register
class LengthCheck(Check):
    name = "length"
    tier = 0

    async def run(self, ctx: Context) -> list[Signal]:
        base = ctx.stats.get("length", {})
        b = base.get(ctx.item.lang)
        if not b or b[2] < 8:
            b = base.get("_all")
        r = log_ratio(ctx.item.source, ctx.translation)
        if not b or r is None:
            return []
        med, sd, _ = b
        z = (r - med) / sd
        ev = {"log_ratio": round(r, 3), "baseline": round(med, 3), "z": round(z, 2)}
        if z < -2.5:
            conf = min(0.6, 0.3 + 0.08 * (-z - 2.5))
            return [Signal(self.name, "omission", "major", conf, f"Translation is unusually short for this language (z={z:.1f}).", None, None, ev)]
        if z > 2.5:
            conf = min(0.6, 0.3 + 0.08 * (z - 2.5))
            return [Signal(self.name, "addition", "major", conf, f"Translation is unusually long for this language (z={z:.1f}).", None, None, ev)]
        return []
