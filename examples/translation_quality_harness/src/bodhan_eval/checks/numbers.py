"""Number, date and unit preservation.

Digits survive translation in every script once folded to ASCII, so the source and
translation multisets are compared directly. The back-translation is a second, noisier
witness: it can explain a missing number (spelled out in words) or confirm a changed one.
"""

from __future__ import annotations

from collections import Counter

import regex

from ..models import Category, Severity, Signal
from ..text.align import similarity
from ..text.numbers import NumberMention, english_number_words, extract_numbers, has_scale_word, multiset, scaled_match
from ..text.segment import words
from .base import ItemContext

SYMBOLS = ("%", "°", "₹", "$", "€", "£", "±", "×", "÷", "≤", "≥", "√", "π")
UNIT_GROUPS = {
    "km": ("km", "kilometre", "kilometer", "kilometres", "kilometers"),
    "m": ("metre", "meter", "metres", "meters"),
    "cm": ("cm", "centimetre", "centimeter", "centimetres", "centimeters"),
    "mm": ("mm", "millimetre", "millimeter", "millimetres", "millimeters"),
    "kg": ("kg", "kilogram", "kilograms", "kilo", "kilos"),
    "g": ("gram", "grams", "gramme"),
    "mg": ("mg", "milligram", "milligrams"),
    "l": ("litre", "liter", "litres", "liters"),
    "ml": ("ml", "millilitre", "milliliter", "millilitres", "milliliters"),
    "celsius": ("celsius", "°c", "centigrade"),
    "fahrenheit": ("fahrenheit", "°f"),
    "hour": ("hour", "hours", "hrs", "hr"),
    "minute": ("minute", "minutes", "min", "mins"),
    "second": ("seconds", "sec", "secs"),
    "day": ("day", "days"),
    "week": ("week", "weeks"),
    "month": ("month", "months"),
    "year": ("year", "years"),
    "percent": ("percent", "per cent", "percentage", "%"),
    "mile": ("mile", "miles"),
    "foot": ("foot", "feet", "ft"),
    "inch": ("inch", "inches"),
    "rupee": ("rupee", "rupees", "rs", "₹", "inr"),
    "dollar": ("dollar", "dollars", "usd", "$"),
    "million": ("million", "millions"),
    "billion": ("billion", "billions"),
    "thousand": ("thousand", "thousands"),
    "lakh": ("lakh", "lakhs"),
    "crore": ("crore", "crores"),
}
_UNIT_LOOKUP = {alias: unit for unit, aliases in UNIT_GROUPS.items() for alias in aliases}


def units_in(text: str) -> Counter:
    found: Counter = Counter()
    lowered = text.lower()
    for m in regex.finditer(r"°[cf]|%|₹|\$|[a-z]+(?: cent)?", lowered):
        unit = _UNIT_LOOKUP.get(m.group(0))
        if unit:
            found[unit] += 1
    return found


def _pair_substitutions(missing: list[NumberMention], extra: list[NumberMention]) -> list[tuple[NumberMention, NumberMention]]:
    pairs, pool = [], list(extra)
    for miss in missing:
        if not pool:
            break
        best = max(pool, key=lambda e: similarity(miss.value, e.value))
        pairs.append((miss, best))
        pool.remove(best)
    return pairs


def check_numbers(ctx: ItemContext) -> None:
    translation = ctx.translation or ""
    with ctx.check("numbers", "deterministic") as res:
        src = extract_numbers(ctx.source)
        tgt = extract_numbers(translation)
        src_counts, tgt_counts = multiset(src), multiset(tgt)
        word_numbers = english_number_words(ctx.source)
        scale = has_scale_word(ctx.source)
        bt_counts = multiset(extract_numbers(ctx.back_translation)) if ctx.back_translation else None
        missing_counts = src_counts - tgt_counts
        extra_counts = tgt_counts - src_counts
        explained = set()
        for value in list(extra_counts):
            if value in word_numbers:
                explained.add(value)
            elif scale and any(scaled_match(value, m) for m in missing_counts):
                explained.add(value)
                for m in list(missing_counts):
                    if scaled_match(value, m):
                        del missing_counts[m]
                        break
        for value in explained:
            del extra_counts[value]
        missing = _take(src, missing_counts)
        extra = _take(tgt, extra_counts)
        res.metrics = {
            "source": sorted(src_counts.elements()),
            "translation": sorted(tgt_counts.elements()),
            "missing": [m.value for m in missing],
            "extra": [e.value for e in extra],
        }
        if bt_counts is not None:
            res.metrics["back_translation"] = sorted(bt_counts.elements())

        pairs = _pair_substitutions(missing, extra)
        paired_missing = {id(m) for m, _ in pairs}
        paired_extra = {id(e) for _, e in pairs}
        for miss, wrong in pairs:
            score = 0.9
            note = ""
            if bt_counts is not None and bt_counts[miss.value] and not bt_counts[wrong.value]:
                score, note = 0.6, " (back-translation still reads the source value)"
            ctx.add(
                Signal(
                    check="numbers",
                    category=Category.NUMBER,
                    score=score,
                    severity=Severity.MAJOR,
                    span=(wrong.start, wrong.end),
                    source_span=(miss.start, miss.end),
                    hard=score >= 0.9,
                    message=f"source number {miss.raw} appears as {wrong.raw} in the translation{note}",
                    data={"source": miss.value, "translation": wrong.value},
                )
            )
        for wrong in (e for e in extra if id(e) not in paired_extra):
            ctx.add(
                Signal(
                    check="numbers",
                    category=Category.NUMBER,
                    score=0.55,
                    severity=Severity.MAJOR,
                    span=(wrong.start, wrong.end),
                    message=f"number {wrong.raw} in the translation is not in the source",
                    data={"translation": wrong.value},
                )
            )
        for miss in (m for m in missing if id(m) not in paired_missing):
            if bt_counts is not None and bt_counts[miss.value]:
                continue  # spelled out in the translation; the round trip recovers it
            score = 0.45 if bt_counts is not None else 0.3
            ctx.add(
                Signal(
                    check="numbers",
                    category=Category.NUMBER,
                    score=score,
                    severity=Severity.MAJOR,
                    source_span=(miss.start, miss.end),
                    message=f"source number {miss.raw} not found in the translation" + (" or its back-translation" if bt_counts is not None else ""),
                    data={"source": miss.value},
                )
            )
        _check_symbols(ctx, translation)
        if ctx.back_translation:
            _check_units(ctx)


def _take(mentions: list[NumberMention], counts: Counter) -> list[NumberMention]:
    remaining = Counter(counts)
    out = []
    for m in mentions:
        if remaining[m.value] > 0:
            out.append(m)
            remaining[m.value] -= 1
    return out


def _check_symbols(ctx: ItemContext, translation: str) -> None:
    for sym in SYMBOLS:
        s, t = ctx.source.count(sym), translation.count(sym)
        if s == t or (sym == "$" and "$" in ctx.source and ("\\" in ctx.source or "$$" in ctx.source)):
            continue
        if s > t:
            pos = ctx.source.find(sym)
            ctx.add(
                Signal(
                    check="numbers",
                    category=Category.NUMBER,
                    score=0.4,
                    severity=Severity.MINOR,
                    source_span=(pos, pos + 1),
                    message=f"symbol {sym} appears {s}x in the source but {t}x in the translation",
                    data={"symbol": sym},
                )
            )
        else:
            pos = translation.find(sym)
            ctx.add(
                Signal(
                    check="numbers",
                    category=Category.NUMBER,
                    score=0.5,
                    severity=Severity.MINOR,
                    span=(pos, pos + 1),
                    message=f"symbol {sym} appears {t}x in the translation but {s}x in the source",
                    data={"symbol": sym},
                )
            )


def _check_units(ctx: ItemContext) -> None:
    src_units, bt_units = units_in(ctx.source), units_in(ctx.back_translation or "")
    missing = src_units - bt_units
    extra = bt_units - src_units
    if missing and extra:
        unit = next(iter(missing))
        pos = next((w.start for w in words(ctx.source) if _UNIT_LOOKUP.get(w.text.lower()) == unit), None)
        ctx.add(
            Signal(
                check="numbers",
                category=Category.NUMBER,
                score=0.55,
                severity=Severity.MAJOR,
                source_span=(pos, pos + len(unit)) if pos is not None else None,
                message=f"unit changed in round trip: source has {sorted(missing)}, back-translation has {sorted(extra)}",
                data={"source_units": sorted(missing), "back_translation_units": sorted(extra)},
            )
        )
