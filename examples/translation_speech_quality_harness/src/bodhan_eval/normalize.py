"""Unicode / Indic-script normalization, tokenization, script detection and number extraction.

Design rule: we never compute spans on normalized text. We tokenize the ORIGINAL string,
keep each token's character offsets, and normalize tokens individually. Any error found on
normalized tokens therefore maps straight back to a span in the original text.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Character classes
# ---------------------------------------------------------------------------

ZWNJ, ZWJ = "\u200c", "\u200d"
# Invisible characters that cause spurious mismatches. ZWJ/ZWNJ change rendering of
# conjuncts but almost never meaning, and ASR never emits them.
INVISIBLE = {"\u200b", ZWNJ, ZWJ, "\u2060", "\ufeff", "\u00ad"}

_PUNCT_MAP = {
    "।": ".",  # DEVANAGARI DANDA (used by many Indic scripts)
    "॥": ".",  # DOUBLE DANDA
    "۔": ".",  # ARABIC FULL STOP (Urdu)
    "،": ",",  # ARABIC COMMA
    "؟": "?",  # ARABIC QUESTION MARK
    "؛": ";",  # ARABIC SEMICOLON
    "“": '"',
    "”": '"',
    "„": '"',
    "«": '"',
    "»": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "–": "-",
    "—": "-",
    "−": "-",
    "‐": "-",
    "‑": "-",
    "\u00a0": " ",
    "\u202f": " ",
    "\u2009": " ",
    "…": "...",
}

# (start, end_inclusive, ISO 15924 script). Sorted by start.
_SCRIPT_RANGES = sorted(
    [
        (0x0041, 0x005A, "Latn"),
        (0x0061, 0x007A, "Latn"),
        (0x00C0, 0x024F, "Latn"),
        (0x1E00, 0x1EFF, "Latn"),
        (0x0600, 0x06FF, "Arab"),
        (0x0750, 0x077F, "Arab"),
        (0x08A0, 0x08FF, "Arab"),
        (0xFB50, 0xFDFF, "Arab"),
        (0xFE70, 0xFEFF, "Arab"),
        (0x0900, 0x097F, "Deva"),
        (0xA8E0, 0xA8FF, "Deva"),
        (0x0980, 0x09FF, "Beng"),
        (0x0A00, 0x0A7F, "Guru"),
        (0x0A80, 0x0AFF, "Gujr"),
        (0x0B00, 0x0B7F, "Orya"),
        (0x0B80, 0x0BFF, "Taml"),
        (0x0C00, 0x0C7F, "Telu"),
        (0x0C80, 0x0CFF, "Knda"),
        (0x0D00, 0x0D7F, "Mlym"),
        (0x1C50, 0x1C7F, "Olck"),
        (0xAAE0, 0xAAFF, "Mtei"),
        (0xABC0, 0xABFF, "Mtei"),
    ]
)
_RANGE_STARTS = [r[0] for r in _SCRIPT_RANGES]


def script_of(ch: str) -> str | None:
    """ISO 15924 script of a letter/mark, or None for digits, punctuation, spaces, symbols."""
    cat = unicodedata.category(ch)
    if cat[0] not in "LM":
        return None
    cp = ord(ch)
    i = bisect.bisect_right(_RANGE_STARTS, cp) - 1
    if i >= 0 and _SCRIPT_RANGES[i][0] <= cp <= _SCRIPT_RANGES[i][1]:
        return _SCRIPT_RANGES[i][2]
    return "Other"


def script_profile(text: str) -> dict[str, float]:
    """Fraction of letters/marks per script. Empty dict if the text has no letters."""
    counts: dict[str, int] = {}
    for ch in text:
        s = script_of(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
    total = sum(counts.values())
    return {s: n / total for s, n in sorted(counts.items())} if total else {}


def dominant_script(text: str) -> str | None:
    prof = script_profile(text)
    return max(prof, key=lambda s: (prof[s], s)) if prof else None


# ---------------------------------------------------------------------------
# Loose folding of Indic spelling variants that ASR / translation produce interchangeably
# (nukta presence, candrabindu vs anusvara). Built from Unicode names, so it covers every
# Brahmic script without a hand-written table.
# ---------------------------------------------------------------------------


def _build_loose_table() -> dict[int, str | None]:
    table: dict[int, str | None] = {}
    by_name = {}
    for cp in range(0x0900, 0x0D80):
        try:
            by_name[unicodedata.name(chr(cp))] = cp
        except ValueError:
            continue
    for name, cp in by_name.items():
        if name.endswith("SIGN NUKTA"):
            table[cp] = None
        elif name.endswith("SIGN CANDRABINDU"):
            anusvara = by_name.get(name.replace("CANDRABINDU", "ANUSVARA"))
            if anusvara:
                table[cp] = chr(anusvara)
    tippi = by_name.get("GURMUKHI TIPPI")
    bindi = by_name.get("GURMUKHI SIGN BINDI")
    if tippi and bindi:
        table[tippi] = chr(bindi)
    return table


_LOOSE_TABLE = _build_loose_table()


# ---------------------------------------------------------------------------
# String normalization
# ---------------------------------------------------------------------------


def to_ascii_digits(text: str) -> str:
    """Map every Unicode decimal digit (२, ৫, ௭, ٣, ...) to ASCII. Length-preserving."""
    return "".join(
        str(unicodedata.decimal(ch)) if ch.isdecimal() and not ch.isascii() else ch for ch in text
    )


def strip_invisible(text: str) -> str:
    return "".join(ch for ch in text if ch not in INVISIBLE)


def normalize_punct(text: str) -> str:
    return "".join(_PUNCT_MAP.get(ch, ch) for ch in text)


def normalize_text(
    text: str,
    *,
    digits: bool = True,
    punct: bool = True,
    invisible: bool = True,
    casefold: bool = True,
    loose: bool = False,
    collapse_space: bool = True,
) -> str:
    """Canonical form for comparison. NOT span-preserving; use tokenize() when spans matter."""
    t = unicodedata.normalize("NFC", text)
    if invisible:
        t = strip_invisible(t)
    if digits:
        t = to_ascii_digits(t)
    if punct:
        t = normalize_punct(t)
    if loose:
        t = unicodedata.normalize("NFC", unicodedata.normalize("NFD", t).translate(_LOOSE_TABLE))
    if casefold:
        t = t.casefold()
    if collapse_space:
        t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Tokenization with offsets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    text: str  # exact substring of the original
    start: int  # char offset in original (inclusive)
    end: int  # char offset in original (exclusive)
    norm: str  # normalized form used for comparison

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)


def _is_word_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LMN" or ch in (ZWJ, ZWNJ)


def tokenize(text: str, *, loose: bool = True) -> list[Token]:
    """Split into word tokens (letters + combining marks + digits), keeping offsets.

    Unlike regex \\w, this keeps Indic vowel signs (category Mc/Mn) inside words, and keeps
    '1,00,000' / '3.5' as single number tokens. Punctuation is dropped.
    """
    tokens: list[Token] = []
    n = len(text)
    i = 0
    while i < n:
        if not _is_word_char(text[i]):
            i += 1
            continue
        j = i + 1
        while j < n:
            ch = text[j]
            if _is_word_char(ch) or (
                ch in ".," and j + 1 < n and text[j - 1].isdecimal() and text[j + 1].isdecimal()
            ):
                j += 1
            else:
                break
        raw = text[i:j]
        norm = normalize_text(raw, punct=False, loose=loose, collapse_space=False)
        if norm:
            tokens.append(Token(raw, i, j, norm))
        i = j
    return tokens


# ---------------------------------------------------------------------------
# Sentence segmentation with offsets
# ---------------------------------------------------------------------------

_SENT_END = re.compile(r"(?<=[.!?।॥۔؟])[\"'”’)\]]*\s+|\n\s*\n|\n(?=\s*[-*+#|>]|\s*\d+[.)]\s)")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence-ish segments as (start, end) offsets. Also splits on blank lines and before
    Markdown block starts, so each table row / list item / heading is its own segment.
    Decimal points ('3.5') never split because a split needs following whitespace."""
    spans = []
    start = 0
    for m in _SENT_END.finditer(text):
        end = m.start() if text[m.start()] in "\n" else m.end()
        seg = text[start:end]
        if seg.strip():
            s = start + (len(seg) - len(seg.lstrip()))
            spans.append((s, start + len(seg.rstrip())))
        start = m.end()
    seg = text[start:]
    if seg.strip():
        s = start + (len(seg) - len(seg.lstrip()))
        spans.append((s, start + len(seg.rstrip())))
    return spans


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberMention:
    value: str  # canonical: ASCII digits, grouping removed, trailing decimal zeros removed
    start: int
    end: int
    raw: str


# A number starts at a digit that is not the tail of another number ("3.5": no match at "5")
# and not glued to a preceding letter ("km2", "CO2", "H2O" are units / names, not numbers).
# "No.1" still counts: a dot only continues a number when a digit precedes it.
_NUM_RE = re.compile(r"(?<!\d)(?<!\d[.,])(?<![^\W\d_])\d+(?:[.,]\d+)*")


def _canonical_number(s: str) -> list[str]:
    """'1,00,000' -> ['100000']; '3.50' -> ['3.5']; '007' -> ['7']; '2,3' -> ['2', '3']."""
    if "," in s:
        groups = s.split(".")[0].split(",")
        is_grouping = all(len(g) in (2, 3) for g in groups[1:]) and len(groups[-1]) == 3
        if is_grouping:
            s = s.replace(",", "")
        else:  # a list like "2,3" -> separate numbers
            return [v for part in s.split(",") for v in _canonical_number(part)]
    if "." in s:
        intpart, _, frac = s.partition(".")
        frac = frac.replace(".", "").rstrip("0")
        intpart = intpart.lstrip("0") or "0"
        return [f"{intpart}.{frac}" if frac else intpart]
    return [s.lstrip("0") or "0"]


def extract_numbers(text: str) -> list[NumberMention]:
    """All numbers in any Unicode digit system, with offsets into the original text."""
    ascii_text = to_ascii_digits(text)  # length-preserving, so offsets stay valid
    out: list[NumberMention] = []
    for m in _NUM_RE.finditer(ascii_text):
        raw = m.group(0).rstrip(".,")
        end = m.start() + len(raw)
        for v in _canonical_number(raw):
            out.append(NumberMention(v, m.start(), end, text[m.start() : end]))
    return out


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------


def spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out
