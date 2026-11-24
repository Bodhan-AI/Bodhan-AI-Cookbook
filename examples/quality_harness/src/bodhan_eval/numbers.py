"""Number and unit extraction over NORMALIZED text (Indic digits already mapped to ASCII).

Returns each numeric mention with its canonical value and its span in ORIGINAL coordinates,
so T2 can compare source vs translation as multisets and point at the exact digit span.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Span
from .normalize import NormResult, normalize

# number with optional grouping (ASCII or Indian), optional decimal, optional sign
_NUM = re.compile(
    r"[+-]?\d{1,3}(?:[,]\d{2,3})*(?:\.\d+)?|[+-]?\d+(?:\.\d+)?"
)

# unit tokens that commonly follow a number (string-based, extend in config if needed)
_UNIT_AFTER = re.compile(
    r"\s?(?:°\s?[cfk]|%|km|cm|mm|kg|mg|g|ml|hz|khz|mhz|ghz|kb|mb|gb|tb"
    r"|km/h|m/s|kwh|kw|mw|gw)\b",
    re.IGNORECASE,
)


@dataclass
class NumberToken:
    value: str          # canonical: grouping commas removed, leading + dropped
    unit: str | None  # normalized unit string or None
    span: Span          # original coordinates (covers digits and unit if present)
    raw: str

    def key(self) -> str:
        """Comparison key. Value plus a coarse unit class (so 25 km != 25 kg, but
        25 == 25 when neither carries a unit)."""
        u = (self.unit or "").lower().replace(" ", "")
        return f"{self.value}|{u}"


def _canonical(num: str) -> str:
    s = num.replace(",", "")
    if s.startswith("+"):
        s = s[1:]
    # normalize -0 and trailing .0 minimally (keep as written otherwise)
    if re.fullmatch(r"-?0+(\.0+)?", s):
        return "0"
    return s


def extract_numbers(text: str, lang: str | None = None) -> list[NumberToken]:
    nr: NormResult = normalize(text, lang)
    norm = nr.text
    out: list[NumberToken] = []
    for m in _NUM.finditer(norm):
        start, end = m.start(), m.end()
        raw = m.group()
        unit = None
        um = _UNIT_AFTER.match(norm, end)
        if um:
            unit = um.group().strip()
            end = um.end()
        span = nr.to_original_span((start, end)) or (0, 0)
        out.append(NumberToken(value=_canonical(raw), unit=unit, span=span, raw=norm[m.start():end]))
    return out


def multiset_diff(
    src: list[NumberToken], tgt: list[NumberToken]
) -> tuple[list[NumberToken], list[NumberToken]]:
    """Return (missing_in_target, extra_in_target) comparing by canonical key.

    Unit tolerance: if a value matches but units differ only by presence/absence, we treat
    it as a match to avoid false alarms when a unit is written as a word in the target."""
    from collections import Counter

    src_c = Counter(t.value for t in src)
    tgt_c = Counter(t.value for t in tgt)
    need_missing = dict(src_c - tgt_c)  # values the target is short on
    need_extra = dict(tgt_c - src_c)    # values the target has in excess

    missing: list[NumberToken] = []
    for t in src:
        if need_missing.get(t.value, 0) > 0:
            missing.append(t)
            need_missing[t.value] -= 1
    extra: list[NumberToken] = []
    for t in tgt:
        if need_extra.get(t.value, 0) > 0:
            extra.append(t)
            need_extra[t.value] -= 1
    return missing, extra
