"""Text normalization shared by every comparison.

Two texts are only compared after the same normalization: NFC, native digits mapped to
ASCII ("२५" -> "25"), zero-width joiners removed, punctuation/symbols dropped, Latin
case-folded and whitespace collapsed. ``normalize_with_map`` keeps a map back to the
original offsets so spans found on normalized text can be reported on the raw text.
"""

from __future__ import annotations

import re
import unicodedata

ZERO_WIDTH = {"​", "‌", "‍", "﻿"}
# Spelling variants that written text and ASR output disagree on, folded before any comparison:
# nukta dropped; candrabindu / tippi -> anusvara; Odia wa (U+0B71) -> va (U+0B35).
NUKTA = {"़", "়", "਼", "઼", "଼", "಼"}
FOLD = {"ँ": "ं", "ঁ": "ং", "ਁ": "ਂ", "ੰ": "ਂ", "ઁ": "ં", "ଁ": "ଂ",
        "ୱ": "ଵ"}
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _map_char(ch: str) -> str:
    if ch in ZERO_WIDTH or ch in NUKTA:
        return ""
    if ch in FOLD:
        return FOLD[ch]
    if ch.isdigit():
        try:
            return str(unicodedata.decimal(ch))
        except ValueError:
            return ch
    cat = unicodedata.category(ch)
    if cat[0] in "PS" or ch.isspace():
        return " "
    return ch.casefold()


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return (normalized, offsets) where offsets[i] is the raw index of normalized char i."""
    text = unicodedata.normalize("NFC", text)
    out: list[str] = []
    offsets: list[int] = []
    for i, ch in enumerate(text):
        for c in _map_char(ch):
            if c == " " and (not out or out[-1] == " "):
                continue
            out.append(c)
            offsets.append(i)
    if out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def ascii_digits(text: str) -> str:
    """NFC plus native digits -> ASCII, keeping everything else (for number extraction)."""
    text = unicodedata.normalize("NFC", text)
    return "".join(str(unicodedata.decimal(c)) if c.isdigit() and unicodedata.decimal(c, None) is not None else c for c in text)


def numbers(text: str) -> list[tuple[str, int, int]]:
    """Numbers as (canonical value, start, end) on the raw text. '1,00,000' and '100000' compare equal."""
    text = ascii_digits(text)
    found = []
    for m in NUMBER.finditer(text):
        value = m.group(0)
        # Thousands separators vary by locale (1,000 / 1,00,000); a trailing ".0" is not a different number.
        canon = re.sub(r",(?=\d{2,3}\b)", "", value).replace(",", ".")
        if "." in canon:
            canon = canon.rstrip("0").rstrip(".") or "0"
        found.append((canon, m.start(), m.end()))
    return found


def script_of(ch: str) -> str | None:
    """Unicode script prefix of a letter (e.g. 'DEVANAGARI'), or None for non-letters."""
    if not (ch.isalpha() or unicodedata.category(ch) in {"Mn", "Mc"}):
        return None
    name = unicodedata.name(ch, "")
    for prefix in ("MEETEI MAYEK", "OL CHIKI"):
        if name.startswith(prefix):
            return prefix
    return name.split(" ")[0] if name else None


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate of hypothesis against reference, both normalized."""
    ref, hyp = normalize(reference).replace(" ", ""), normalize(hypothesis).replace(" ", "")
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, hc in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / len(ref)
