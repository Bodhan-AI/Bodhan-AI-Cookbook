"""Normalization with an offset map back to original coordinates.

Comparing "२५" with "25", Unicode variants and punctuation must be normalized before any
error rate means anything -- but flags must point at the ORIGINAL text. So every normalized
character carries the [start, end) range of the raw characters it came from, and
`to_original_span` maps a normalized span back to raw coordinates.

The only approximate step is NFC (it can change length for exotic inputs); it is handled
with a difflib alignment and is exact whenever NFC preserves length, which covers the
scripts we target. Everything after NFC is exact, per-character bookkeeping.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import Span

# code points stripped for comparison only (kept in the original for display/spans)
_ZERO_WIDTH = {
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x00AD,
}

_PUNCT_MAP = {
    "।": ".",  # devanagari danda
    "॥": ".",  # devanagari double danda
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ",  # nbsp
}

# punctuation removed in the ASR-comparison form
_ASR_STRIP = set(".,!?;:\"'()[]{}-–—…/\\|`~@#%^&*_+=<>")


@dataclass
class NormResult:
    original: str
    text: str
    starts: list[int]  # starts[i] = raw index where normalized char i begins
    ends: list[int]    # ends[i]   = raw index (exclusive) where normalized char i ends

    def to_original_span(self, norm_span: Span) -> Span | None:
        ns, ne = norm_span
        ns = max(0, min(ns, len(self.text)))
        ne = max(0, min(ne, len(self.text)))
        if ne <= ns:
            start = self.starts[ns] if ns < len(self.starts) else len(self.original)
            return (start, start)
        return (self.starts[ns], self.ends[ne - 1])


def _nfc_with_map(raw: str) -> list[tuple[str, int, int]]:
    """Return list of (char, raw_start, raw_end) after NFC."""
    base = unicodedata.normalize("NFC", raw)
    if len(base) == len(raw):
        return [(ch, i, i + 1) for i, ch in enumerate(base)]
    # rare length-changing case: align base->raw with difflib opcodes
    out: list[tuple[str, int, int]] = []
    sm = SequenceMatcher(a=raw, b=base, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                out.append((base[j1 + k], i1 + k, i1 + k + 1))
        else:
            # map every produced base char to the whole replaced raw range
            for k in range(j1, j2):
                out.append((base[k], i1, max(i1 + 1, i2)))
    return out


def _normalize_chars(raw: str, *, for_asr: bool) -> list[tuple[str, int, int]]:
    chars = _nfc_with_map(raw)
    out: list[tuple[str, int, int]] = []
    for ch, s, e in chars:
        cp = ord(ch)
        if cp in _ZERO_WIDTH:
            continue
        # Indic / Arabic digit -> ASCII
        if not ch.isascii() and ch.isdigit():
            val = None
            try:
                val = unicodedata.decimal(ch)
            except (TypeError, ValueError):
                try:
                    val = unicodedata.digit(ch)
                except (TypeError, ValueError):
                    val = None
            if val is not None:
                out.append((str(val), s, e))
                continue
        # punctuation unification
        if ch in _PUNCT_MAP:
            out.append((_PUNCT_MAP[ch], s, e))
            continue
        out.append((ch, s, e))

    # whitespace collapse (and strip ends), tracking spans
    collapsed: list[tuple[str, int, int]] = []
    i = 0
    n = len(out)
    while i < n:
        ch, s, e = out[i]
        if ch.isspace():
            j = i
            last_e = e
            while j < n and out[j][0].isspace():
                last_e = out[j][2]
                j += 1
            collapsed.append((" ", s, last_e))
            i = j
        else:
            collapsed.append((ch, s, e))
            i += 1
    # strip leading/trailing single spaces
    while collapsed and collapsed[0][0] == " ":
        collapsed.pop(0)
    while collapsed and collapsed[-1][0] == " ":
        collapsed.pop()

    if not for_asr:
        return collapsed

    # ASR form: lowercase Latin, drop punctuation
    asr: list[tuple[str, int, int]] = []
    for ch, s, e in collapsed:
        if ch in _ASR_STRIP:
            continue
        asr.append((ch.lower(), s, e))
    # re-collapse any spaces exposed by punctuation removal
    final: list[tuple[str, int, int]] = []
    for ch, s, e in asr:
        if ch == " " and final and final[-1][0] == " ":
            prev = final[-1]
            final[-1] = (" ", prev[1], e)
            continue
        final.append((ch, s, e))
    while final and final[0][0] == " ":
        final.pop(0)
    while final and final[-1][0] == " ":
        final.pop()
    return final


def _build(raw: str, for_asr: bool) -> NormResult:
    chars = _normalize_chars(raw, for_asr=for_asr)
    text = "".join(c for c, _, _ in chars)
    starts = [s for _, s, _ in chars]
    ends = [e for _, _, e in chars]
    return NormResult(original=raw, text=text, starts=starts, ends=ends)


def normalize(text: str, lang: str | None = None) -> NormResult:
    """General comparison form: NFC, zero-width stripped, digits->ASCII, punctuation unified,
    whitespace collapsed. Case preserved (scripts are caseless; Latin case can matter)."""
    return _build(text or "", for_asr=False)


def norm_for_compare(text: str, lang: str | None = None) -> str:
    return normalize(text, lang).text


def norm_for_asr(text: str, lang: str | None = None) -> str:
    """ASR-comparison form: also lowercases Latin and removes punctuation."""
    return _build(text or "", for_asr=True).text
