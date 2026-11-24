"""text.normalize: Reversible normalization for comparing Indic (and English) text.
text.protected: Protected regions: code, LaTeX math, URLs and Markdown link targets.
text.scripts: Script detection from Unicode Script properties (the ``regex`` module).
text.segment: Sentence and clause segmentation that returns character spans.
text.entities: Cheap English named-entity finder (capitalised word runs), shared by the round-trip
spans: Span helpers. Text spans are [start, end) character offsets into the original
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

import regex

from .config import LANGUAGES

# -------------------- normalize
ZERO_WIDTH = frozenset("\u200b\u200c\u200d\ufeff\u2060")


CHILLU = {
    "\u0d23\u0d4d\u200d": "\u0d7a",
    "\u0d28\u0d4d\u200d": "\u0d7b",
    "\u0d30\u0d4d\u200d": "\u0d7c",
    "\u0d32\u0d4d\u200d": "\u0d7d",
    "\u0d33\u0d4d\u200d": "\u0d7e",
    "\u0d15\u0d4d\u200d": "\u0d7f",
}


PUNCT_FOLD = {
    "\u0964": ".",  # devanagari danda (used by hi, mr, bn, as, pa, or ...)
    "\u0965": ".",  # double danda
    "\u06d4": ".",  # arabic full stop (Urdu)
    "\u061f": "?",  # arabic question mark
    "\u060c": ",",  # arabic comma
    "\u061b": ";",  # arabic semicolon
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "–": "-", "—": "-", "−": "-",
    "…": "...",
}


ARABIC_FOLD = {
    "\u064a": "\u06cc",  # arabic yeh → farsi/urdu yeh
    "\u0649": "\u06cc",  # alef maksura → urdu yeh
    "\u0643": "\u06a9",  # arabic kaf → keheh
    "\u0647": "\u06c1",  # heh → heh goal
}


ARABIC_DROP = frozenset([chr(c) for c in range(0x064B, 0x0660)] + ["\u0670", "\u0640"])  # harakat, superscript alef, tatweel


def _is_attached(ch: str) -> bool:
    return unicodedata.category(ch).startswith("M") or ch in ZERO_WIDTH


def chunks(s: str) -> Iterator[tuple[int, int]]:
    """Yield (start, end) chunks: one base character plus trailing marks / zero-width chars."""
    i, n = 0, len(s)
    while i < n:
        j = i + 1
        while j < n and _is_attached(s[j]):
            j += 1
        yield i, j
        i = j


def _fold_char(c: str, casefold_latin: bool) -> str:
    if c in ZERO_WIDTH or c in ARABIC_DROP:
        return ""
    if c in PUNCT_FOLD:
        return PUNCT_FOLD[c]
    if c in ARABIC_FOLD:
        return ARABIC_FOLD[c]
    cat = unicodedata.category(c)
    if cat == "Nd":
        return str(unicodedata.decimal(c))
    if "！" <= c <= "～":  # fullwidth ASCII variants
        return unicodedata.normalize("NFKC", c)
    if c.isspace():
        return " "
    if casefold_latin and cat.startswith("L") and ord(c) < 0x0250:
        return c.casefold()
    return c


def _is_skeleton_noise(c: str) -> bool:
    cat = unicodedata.category(c)
    return c == " " or cat.startswith("P") or cat.startswith("S")


@dataclass(frozen=True)
class NormText:
    original: str
    norm: str
    omap: tuple[tuple[int, int], ...]

    def to_orig(self, start: int, end: int) -> tuple[int, int] | None:
        """[start, end) in normalized text → [start, end) in the original (chunk precision)."""
        if not self.omap or start >= end or start < 0:
            return None
        end = min(end, len(self.omap))
        if start >= end:
            return None
        return self.omap[start][0], self.omap[end - 1][1]

    def to_norm(self, start: int, end: int) -> tuple[int, int] | None:
        """[start, end) in the original text → covering [start, end) in normalized text."""
        if not self.omap or start >= end:
            return None
        ends = [b for _, b in self.omap]
        starts = [a for a, _ in self.omap]
        k0 = bisect.bisect_right(ends, start)  # first norm char whose chunk ends after `start`
        k1 = bisect.bisect_left(starts, end)  # first norm char whose chunk starts at/after `end`
        return (k0, k1) if k0 < k1 else None

    def find_all(self, needle_norm: str) -> list[int]:
        out, k = [], self.norm.find(needle_norm) if needle_norm else -1
        while k >= 0:
            out.append(k)
            k = self.norm.find(needle_norm, k + 1)
        return out


def normalize_with_map(s: str, *, casefold_latin: bool = True, skeleton: bool = False) -> NormText:
    """Normalize ``s`` keeping an offset map.

    ``skeleton=True`` additionally drops all punctuation, symbols and spaces,
    used as a tolerant second pass when matching quotes.
    """
    out: list[str] = []
    omap: list[tuple[int, int]] = []
    for a, b in chunks(s):
        piece = s[a:b]
        if "\u200d" in piece:
            for old, new in CHILLU.items():
                piece = piece.replace(old, new)
        piece = unicodedata.normalize("NFC", piece)
        for c in piece:
            for f in _fold_char(c, casefold_latin):
                if skeleton and _is_skeleton_noise(f):
                    continue
                if f == " " and (not out or out[-1] == " "):
                    continue
                out.append(f)
                omap.append((a, b))
    if out and out[-1] == " ":
        out.pop()
        omap.pop()
    return NormText(s, "".join(out), tuple(omap))


def normalize(s: str, **kw) -> str:
    return normalize_with_map(s, **kw).norm


@dataclass(frozen=True)
class QuoteMatch:
    span: tuple[int, int]  # original offsets
    method: str  # "exact" | "normalized" | "skeleton"
    occurrences: int  # >1 means the quote is ambiguous; the first one is returned


def quote_to_span(original: str, quote: str) -> QuoteMatch | None:
    """Locate a verbatim quote (e.g. from the LLM judge) in ``original``.

    Tries: exact substring → normalized match → skeleton match (ignoring
    punctuation/spaces). Never accepts a paraphrase; returns None if not found.
    """
    if not quote or not quote.strip():
        return None
    q = quote.strip()
    k = original.find(q)
    if k >= 0:
        return QuoteMatch((k, k + len(q)), "exact", original.count(q))
    for method, skel in (("normalized", False), ("skeleton", True)):
        nt = normalize_with_map(original, skeleton=skel)
        nq = normalize_with_map(q, skeleton=skel).norm
        if not nq or (skel and len(nq) < 2):
            continue
        hits = nt.find_all(nq)
        if hits:
            span = nt.to_orig(hits[0], hits[0] + len(nq))
            if span:
                return QuoteMatch(span, method, len(hits))
    return None


# -------------------- protected
@dataclass(frozen=True)
class Region:
    start: int
    end: int
    kind: str  # code_block | math_display | math_env | math_inline | code_inline | url | link_target


_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("code_block", re.compile(r"^[ \t]*(```|~~~)[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.M | re.S), 0),
    ("math_display", re.compile(r"\$\$.+?\$\$", re.S), 0),
    ("math_env", re.compile(r"\\begin\{([^}]+)\}.*?\\end\{\1\}", re.S), 0),
    ("math_display", re.compile(r"\\\[.+?\\\]", re.S), 0),
    ("math_inline", re.compile(r"\\\(.+?\\\)", re.S), 0),
    # $...$ : no space just inside the dollars and no digit right after the closing one,
    # so currency such as "$5 and $10" is not taken as math.
    ("math_inline", re.compile(r"(?<![\\$])\$(?=\S)[^$\n]+?(?<=[^\s\\])\$(?!\d)"), 0),
    ("code_inline", re.compile(r"`[^`\n]+`"), 0),
    ("link_target", re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)"), 1),
    ("url", re.compile(r"https?://[^\s<>()\[\]]+"), 0),
]


def find_protected(text: str) -> list[Region]:
    claimed: list[Region] = []
    for kind, pat, group in _PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span(group)
            if s >= e or any(s < r.end and e > r.start for r in claimed):
                continue
            claimed.append(Region(s, e, kind))
    return sorted(claimed, key=lambda r: (r.start, r.end))


def mask_protected(text: str, regions: list[Region] | None = None, fill: str = " ") -> str:
    """Replace protected regions by ``fill`` (same length, so offsets are unchanged)."""
    regions = find_protected(text) if regions is None else regions
    chars = list(text)
    for r in regions:
        chars[r.start : r.end] = fill * (r.end - r.start)
    return "".join(chars)


def in_regions(pos: int, regions: list[Region]) -> bool:
    return any(r.start <= pos < r.end for r in regions)


# -------------------- scripts
KNOWN_SCRIPTS: tuple[str, ...] = tuple(
    sorted(
        {lang.script for lang in LANGUAGES.values()}
        | {"Latin", "Cyrillic", "Greek", "Han", "Hiragana", "Katakana", "Hangul", "Thai", "Hebrew", "Sinhala", "Tibetan", "Myanmar", "Ethiopic"}
    )
)


_SCRIPT_RE = {name: regex.compile(rf"\p{{Script={name}}}") for name in KNOWN_SCRIPTS}


_WORD_RE = regex.compile(r"[\p{L}\p{M}\p{N}]+(?:['’\-][\p{L}\p{M}\p{N}]+)*")


@lru_cache(maxsize=4096)
def char_script(c: str) -> str:
    for name, pat in _SCRIPT_RE.items():
        if pat.match(c):
            return name
    return "Other"


def _excluded(i: int, exclude: list[Region]) -> bool:
    return any(r.start <= i < r.end for r in exclude)


@dataclass(frozen=True)
class ScriptProfile:
    counts: dict[str, int]
    letters: int

    @property
    def dominant(self) -> str | None:
        if not self.counts:
            return None
        return sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    def ratio(self, script: str) -> float:
        return self.counts.get(script, 0) / self.letters if self.letters else 0.0


def script_profile(text: str, exclude: list[Region] | None = None) -> ScriptProfile:
    exclude = exclude or []
    counts: Counter[str] = Counter()
    for i, c in enumerate(text):
        if not unicodedata.category(c).startswith("L") or (exclude and _excluded(i, exclude)):
            continue
        counts[char_script(c)] += 1
    return ScriptProfile(dict(counts), sum(counts.values()))


def expected_script(lang: str) -> str:
    return LANGUAGES[lang].script


@dataclass(frozen=True)
class Run:
    start: int
    end: int
    words: int


def foreign_runs(text: str, script: str, *, exclude: list[Region] | None = None, min_words: int = 3) -> list[Run]:
    """Runs of consecutive words written in ``script`` (e.g. Latin inside a Hindi output).

    Words that are pure digits neither break nor extend a run; any word in
    another script, or a protected region, breaks it.
    """
    exclude = exclude or []
    runs: list[Run] = []
    cur_start = cur_end = None
    count = 0

    def close():
        nonlocal cur_start, cur_end, count
        if cur_start is not None and count >= min_words:
            runs.append(Run(cur_start, cur_end, count))
        cur_start = cur_end = None
        count = 0

    last_end = 0
    for m in _WORD_RE.finditer(text):
        s, e = m.span()
        between = any(last_end <= r.start < s for r in exclude)  # a protected region sits between words
        inside = any(r.start < e and r.end > s for r in exclude)  # this word is (partly) protected
        last_end = e
        if between or inside:
            close()
        if inside:
            continue
        letters = [char_script(c) for c in m.group() if unicodedata.category(c).startswith("L")]
        if not letters:
            continue  # number: neutral
        if all(x == script for x in letters):
            if cur_start is None:
                cur_start = s
            cur_end = e
            count += 1
        else:
            close()
    close()
    return runs


# -------------------- segment
TERMINATORS = frozenset(".?!\u0964\u0965\u06d4\u061f")


CLOSERS = frozenset("\"'”’)]»")


CLAUSE_SEPARATORS = frozenset(",;:\u060c\u061b")


def _trim(text: str, s: int, e: int) -> tuple[int, int] | None:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return (s, e) if s < e else None


def _split(text: str, start: int, end: int, seps: frozenset[str], regions: list[Region], newline_breaks: bool) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    seg_start = start
    i = start
    while i < end:
        region = next((r for r in regions if r.start <= i < r.end), None)
        if region is not None:
            i = region.end
            continue
        c = text[i]
        boundary = None
        if newline_breaks and c == "\n":
            boundary = i
        elif c in seps:
            j = i + 1
            while j < end and (text[j] in seps or text[j] in CLOSERS):
                j += 1
            if j >= end or text[j].isspace():
                boundary = j
                i = j - 1
        if boundary is not None:
            span = _trim(text, seg_start, boundary)
            if span:
                out.append(span)
            seg_start = boundary
        i += 1
    span = _trim(text, seg_start, end)
    if span:
        out.append(span)
    return out


def sentences(text: str, regions: list[Region] | None = None) -> list[tuple[int, int]]:
    regions = find_protected(text) if regions is None else regions
    return _split(text, 0, len(text), TERMINATORS, regions, newline_breaks=True)


def clauses(text: str, span: tuple[int, int] | None = None, regions: list[Region] | None = None) -> list[tuple[int, int]]:
    """Split a sentence span (default: whole text) further at commas / semicolons / colons."""
    regions = find_protected(text) if regions is None else regions
    s, e = span or (0, len(text))
    return _split(text, s, e, CLAUSE_SEPARATORS | TERMINATORS, regions, newline_breaks=True)


# -------------------- entities
_ENTITY = re.compile(r"\b[A-Z][a-zA-Z'\-]+(?:\s+(?:of\s+|de\s+|the\s+)?[A-Z][a-zA-Z'\-]+)*")


_SENT_START = re.compile(r"(?:(?:^|[.!?]\s+|\n)[\s#>*\-|]*[\"'(]?|\|\s*)$")


def english_entities(source: str) -> list[str]:
    """Capitalised word runs that do not start a sentence (a cheap English NER).
    Entities that start a sentence are skipped: there the capital is not evidence."""
    out: list[str] = []
    masked = source
    for r in find_protected(source):
        masked = masked[: r.start] + " " * (r.end - r.start) + masked[r.end :]
    for m in _ENTITY.finditer(masked):
        if _SENT_START.search(masked[: m.start()]):
            continue
        ent = m.group().strip()
        if len(ent) >= 3 and ent not in out:
            out.append(ent)
    return out


# -------------------- spans
Span = tuple[float, float]


def overlaps(a: Span, b: Span) -> bool:
    """True if the spans share any length. A zero-width span (an omission point)
    overlaps a span that contains it or touches/adjoins it (within 1 character)."""
    if a[0] == a[1] or b[0] == b[1]:
        p, other = (a, b) if a[0] == a[1] else (b, a)
        tol = 1.0 if isinstance(other[0], int) and isinstance(other[1], int) else 0.0
        return (other[0] - tol) <= p[0] <= (other[1] + tol)
    return min(a[1], b[1]) > max(a[0], b[0])


def iou(a: Span, b: Span) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    if union <= 0:
        return 1.0 if a == b else 0.0
    return inter / union


def union_span(spans: list[Span]) -> Span | None:
    spans = [s for s in spans if s is not None]
    if not spans:
        return None
    return min(s[0] for s in spans), max(s[1] for s in spans)


def pad_span(span: Span, pad: float, limit: float) -> Span:
    return max(0, span[0] - pad), min(limit, span[1] + pad)
