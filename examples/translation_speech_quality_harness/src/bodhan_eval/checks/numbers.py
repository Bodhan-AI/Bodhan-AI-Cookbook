"""Number and unit preservation.

Numbers are compared as multisets after digit normalization (२५ / ২৫ / ௨௫ -> 25). The
back-translation is used to separate real errors from legitimate rewrites: if a number vanished
from the digits but the back-translation still says it (e.g. it was spelled out in words), the
signal is kept only as weak evidence.
"""
from __future__ import annotations

from collections import Counter

import regex

from ..text.markup import math_segments
from ..text.numbers import english_number_values, extract_numbers
from .base import Check, Context, Signal, register

UNIT_GROUPS = {
    "temperature": {"celsius": ["°c", "celsius", "centigrade"], "fahrenheit": ["°f", "fahrenheit"], "kelvin": ["kelvin"]},
    "length": {"km": ["km", "kilometre", "kilometer"], "m": ["metre", "meter"], "cm": ["cm", "centimetre", "centimeter"],
               "mm": ["mm", "millimetre", "millimeter"], "mile": ["mile"], "inch": ["inch"], "foot": ["feet", "foot"]},
    "mass": {"kg": ["kg", "kilogram"], "g": ["gram"], "mg": ["mg", "milligram"], "tonne": ["tonne", "ton"], "pound": ["pound", "lb"]},
    "time": {"second": ["second"], "minute": ["minute"], "hour": ["hour"], "day": ["day"], "week": ["week"], "month": ["month"],
             "year": ["year"]},
    "volume": {"litre": ["litre", "liter"], "ml": ["ml", "millilitre", "milliliter"]},
    "percent": {"percent": ["%", "percent", "per cent"]},
    "currency": {"rupee": ["₹", "rupee", "rs."], "dollar": ["$", "dollar"]},
}


def units_in(text: str) -> dict[str, set[str]]:
    t = text.lower()
    found: dict[str, set[str]] = {}
    for group, units in UNIT_GROUPS.items():
        for unit, forms in units.items():
            for f in forms:
                pat = regex.escape(f) if not f[0].isalpha() else rf"(?<![a-z]){regex.escape(f)}"
                if regex.search(pat, t):
                    found.setdefault(group, set()).add(unit)
                    break
    return found


def _outside_math(nums, text):
    segs = math_segments(text)
    return [n for n in nums if not any(s <= n.start < e for s, e, _ in segs)]


@register
class NumberCheck(Check):
    name = "numbers"
    tier = 0

    def applies(self, ctx: Context) -> bool:
        return bool(ctx.translation) and (bool(extract_numbers(ctx.item.source)) or bool(extract_numbers(ctx.translation)))

    async def run(self, ctx: Context) -> list[Signal]:
        src, tr = ctx.item.source, ctx.translation
        src_nums = _outside_math(extract_numbers(src), src)
        tr_nums = _outside_math(extract_numbers(tr), tr)
        sc, tc = Counter(n.value for n in src_nums), Counter(n.value for n in tr_nums)
        missing, extra = sc - tc, tc - sc
        spelled = english_number_values(src) - sc  # numbers written as words in the source
        extra = Counter({v: c for v, c in extra.items() if spelled.get(v, 0) < c} )
        bt = ctx.artifacts.get("backtranslation")
        bt_vals = english_number_values(bt) if bt else Counter()
        out: list[Signal] = []

        miss_list = [n for n in src_nums if missing[n.value] > 0]
        extra_list = [n for n in tr_nums if extra[n.value] > 0]
        # one-for-one substitution (100 -> 10): the clearest case, localized on the wrong number
        while miss_list and extra_list:
            m, x = miss_list.pop(0), extra_list.pop(0)
            missing[m.value] -= 1
            extra[x.value] -= 1
            # a one-for-one digit change is deterministic evidence; a disagreeing back-translation is checker noise
            # (it has been seen silently "correcting" a corrupted number back to the source value)
            conf = 0.98 if bt_vals.get(x.value) else 0.96
            out.append(Signal(self.name, "number", _sev(m.value, x.value), conf, f"Source number {m.raw!r} appears as {x.raw!r} in the translation.",
                              (x.start, x.end), (m.start, m.end), {"source": m.value, "translation": x.value, "back_translation_has": bool(bt_vals.get(x.value))}))
        for m in miss_list:
            if bt and bt_vals.get(m.value):
                conf, note = 0.2, "the back-translation still contains it, so it was probably written in words"
            else:
                conf, note = (0.85 if bt else 0.7), "not found in digits" + (" or in the back-translation" if bt else "")
            out.append(Signal(self.name, "number", "major", conf, f"Source number {m.raw!r} is missing from the translation ({note}).",
                              None, (m.start, m.end), {"source": m.value}))
        for x in extra_list:
            out.append(Signal(self.name, "number", "major", 0.8, f"The translation contains a number {x.raw!r} that is not in the source.",
                              (x.start, x.end), None, {"translation": x.value}))
        # units, compared in English through the back-translation (noisy, so moderate confidence)
        if bt:
            su, bu = units_in(src), units_in(bt)
            for group, units in su.items():
                if group in bu and not (units & bu[group]):
                    out.append(Signal(self.name, "number", "major", 0.55, f"Unit changed: source has {sorted(units)}, back-translation has {sorted(bu[group])}.",
                                      None, None, {"source_units": sorted(units), "bt_units": sorted(bu[group])}))
        return out


def _sev(a: str, b: str) -> str:
    try:
        fa, fb = float(a), float(b)
        if fa == 0 or abs(fa - fb) / abs(fa) >= 0.1:
            return "critical"
    except ValueError:
        pass
    return "major"
