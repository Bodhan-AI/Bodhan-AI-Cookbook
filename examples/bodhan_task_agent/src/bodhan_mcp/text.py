"""Text utilities: chunking for model limits, normalisation, and verification metrics.

All of this is deterministic plain code. It never writes Indian-language text; it
only splits, joins and measures text that came from Bodhan models or the user.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from . import pricing

# --------------------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------------------

_DIGIT_BLOCKS = [0x0660, 0x06F0, 0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66, 0xABF0, 0x1C50]
_DIGIT_MAP = {base + i: str(i) for base in _DIGIT_BLOCKS for i in range(10)}
_ZERO_WIDTH = dict.fromkeys([0x200B, 0x200C, 0x200D, 0xFEFF], None)


def ascii_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def normalize_for_compare(text: str) -> str:
    """NFC, ASCII digits, no zero-width chars, no punctuation/symbols, lower-case, single spaces."""
    t = unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH)
    t = ascii_digits(t).lower()
    t = "".join(" " if unicodedata.category(ch)[0] in {"P", "S"} else ch for ch in t)
    return re.sub(r"\s+", " ", t).strip()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate on normalised text, ignoring spaces. 0 = identical."""
    r = normalize_for_compare(reference).replace(" ", "")
    h = normalize_for_compare(hypothesis).replace(" ", "")
    if not r:
        return 0.0 if not h else 1.0
    return round(levenshtein(r, h) / len(r), 4)


def numbers(text: str) -> Counter[str]:
    """Multiset of numbers (any script's digits), normalised: '1,200' == '1200', '२५' == '25'."""
    t = ascii_digits(unicodedata.normalize("NFC", text))
    found = re.findall(r"\d+(?:[.,]\d+)*", t)
    return Counter(n.replace(",", "") for n in found)


def repeated_ngrams(text: str, n: int = 4, min_count: int = 3) -> list[str]:
    words = normalize_for_compare(text).split()
    grams = Counter(" ".join(words[i: i + n]) for i in range(max(0, len(words) - n + 1)))
    return [g for g, c in grams.items() if c >= min_count]


# --------------------------------------------------------------------------------------
# Markdown structure
# --------------------------------------------------------------------------------------

TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
HTML_TABLE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)


def table_shapes(md: str) -> list[tuple[int, int]]:
    """(rows, columns) for every Markdown pipe table and HTML table, in order."""
    shapes: list[tuple[int, int]] = []
    rows: list[str] = []

    def flush() -> None:
        if rows:
            body = [r for r in rows if not re.fullmatch(r"\s*\|[\s:|-]+\|\s*", r)]
            cols = max((r.strip().strip("|").count("|") + 1 for r in body), default=0)
            shapes.append((len(body), cols))
            rows.clear()

    for line in md.splitlines():
        if TABLE_LINE.match(line):
            rows.append(line)
        else:
            flush()
    flush()
    for m in HTML_TABLE.finditer(md):
        t = m.group(0)
        nrows = len(re.findall(r"<tr\b", t, re.IGNORECASE))
        first = re.search(r"<tr\b.*?</tr>", t, re.IGNORECASE | re.DOTALL)
        ncols = len(re.findall(r"<t[dh]\b", first.group(0), re.IGNORECASE)) if first else 0
        shapes.append((nrows, ncols))
    return shapes


_SEPARATOR_ROW = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def split_tables(md: str) -> list[tuple[str, str]]:
    """Split Markdown into ordered ("table", block) and ("prose", block) parts.

    Only pipe tables are separated out (they can be rebuilt cell by cell); HTML tables
    stay inside prose and are protected by chunking instead.
    """
    parts: list[tuple[str, str]] = []
    prose: list[str] = []
    for block in markdown_blocks(md):
        lines = block.splitlines()
        if lines and all(TABLE_LINE.match(ln) for ln in lines) and len(lines) >= 2:
            if prose:
                parts.append(("prose", "\n\n".join(prose)))
                prose = []
            parts.append(("table", block))
        else:
            prose.append(block)
    if prose:
        parts.append(("prose", "\n\n".join(prose)))
    return parts


def parse_pipe_table(block: str) -> list[list[str] | None]:
    """Rows of cell strings; ``None`` marks a header separator row (kept verbatim on rebuild)."""
    rows: list[list[str] | None] = []
    for line in block.splitlines():
        if _SEPARATOR_ROW.match(line) and "-" in line:
            rows.append(None)
            continue
        inner = line.strip()
        inner = inner[1:] if inner.startswith("|") else inner
        inner = inner[:-1] if inner.endswith("|") else inner
        rows.append([c.strip() for c in re.split(r"(?<!\\)\|", inner)])
    return rows


def render_pipe_table(rows: list[list[str] | None]) -> str:
    width = max((len(r) for r in rows if r is not None), default=1)
    out = []
    for r in rows:
        if r is None:
            out.append("|" + "|".join(["---"] * width) + "|")
        else:
            cells = [c.replace("\n", " ") for c in r] + [""] * (width - len(r))
            out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def table_structure(block: str) -> list[int | None]:
    """Per row: the number of cells, or None for a header separator. Two tables with equal structure
    render identically; this catches a dropped `|---|` row, which table_shapes deliberately ignores."""
    return [None if r is None else len(r) for r in parse_pipe_table(block.strip())]


def needs_translation(cell: str) -> bool:
    """A cell is worth translating only if it has letters (numbers, symbols and blanks pass through)."""
    return any(ch.isalpha() for ch in cell)


def heading_count(md: str) -> int:
    return len(re.findall(r"^\s{0,3}#{1,6}\s", md, flags=re.MULTILINE))


def markdown_blocks(md: str) -> list[str]:
    """Split Markdown into blocks at blank lines, keeping tables and fenced code intact."""
    blocks: list[str] = []
    cur: list[str] = []
    in_fence = False
    in_html_table = False
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        if re.search(r"<table\b", stripped, re.IGNORECASE):
            in_html_table = True
        if re.search(r"</table>", stripped, re.IGNORECASE):
            in_html_table = False
            cur.append(line)
            continue
        if not stripped and not in_fence and not in_html_table:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


# --------------------------------------------------------------------------------------
# Chunking for model limits
# --------------------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?।॥…])\s+|\n+")
_CLAUSE = re.compile(r"(?<=[,;:،—–])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    """Last resort for a clause longer than max_chars: break at whitespace, or mid-word only if unavoidable."""
    out: list[str] = []
    cur = ""
    for word in text.split():
        while len(word) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:max_chars])
            word = word[max_chars:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur += " " + word
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out


def split_for_tts(text: str, max_chars: int = 220) -> list[str]:
    """Pieces of at most ``max_chars`` (Indic-Speak wants a sentence or two per request).

    Splits at sentence ends first, then clause punctuation, then whitespace, and packs
    adjacent short sentences together up to the limit. No characters other than
    whitespace are dropped, which ``test_text.py`` verifies.
    """
    pieces: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue
        for clause in (c.strip() for c in _CLAUSE.split(sentence) if c.strip()):
            pieces.extend([clause] if len(clause) <= max_chars else _hard_wrap(clause, max_chars))
    packed: list[str] = []
    for p in pieces:
        if packed and len(packed[-1]) + 1 + len(p) <= max_chars:
            packed[-1] = f"{packed[-1]} {p}"
        else:
            packed.append(p)
    return packed


def chunk_for_translation(md: str, max_tokens: int = 1500) -> list[str]:
    """Group Markdown blocks into chunks under ``max_tokens``, never splitting a table or code fence.

    Indic-Translate is validated to 32K tokens of context, but long single requests risk
    truncation and slow retries; ~1.5K-token chunks of whole paragraphs keep context and
    let chunks run concurrently. An oversized prose block is split at sentence ends.
    """
    chunks: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    for block in markdown_blocks(md):
        t = pricing.estimate_tokens(block)
        is_structured = bool(TABLE_LINE.match(block.splitlines()[0])) or "<table" in block.lower() or block.lstrip().startswith("```")
        parts = [block]
        if t > max_tokens and not is_structured:
            parts, buf = [], ""
            for s in split_sentences(block):
                if buf and pricing.estimate_tokens(buf + " " + s) > max_tokens:
                    parts.append(buf)
                    buf = s
                else:
                    buf = f"{buf} {s}".strip()
            if buf:
                parts.append(buf)
        for part in parts:
            pt = pricing.estimate_tokens(part)
            if cur and cur_tokens + pt > max_tokens:
                chunks.append("\n\n".join(cur))
                cur, cur_tokens = [], 0
            cur.append(part)
            cur_tokens += pt
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def markdown_to_speech(md: str) -> str:
    """Turn Markdown into plain sentences for TTS: drop markup, read table rows as comma lists."""
    lines: list[str] = []
    for line in md.splitlines():
        s = line.strip()
        if not s or re.fullmatch(r"\|?[\s:|-]+\|?", s) or s.startswith("```"):
            continue
        if TABLE_LINE.match(s):
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
            s = ", ".join(cells)
        s = re.sub(r"<[^>]+>", " ", s)  # HTML tags
        s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)  # images
        s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)  # links -> text
        s = re.sub(r"^#{1,6}\s*", "", s)
        s = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", s)
        s = re.sub(r"[*_`~]+", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            if not re.search(r"[.!?।॥:;,]$", s):
                s += "."
            lines.append(s)
    return " ".join(lines)


# --------------------------------------------------------------------------------------
# Subtitles
# --------------------------------------------------------------------------------------

def _ts(seconds: float, sep: str = ",") -> str:
    ms = round(max(seconds, 0.0) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def wrap_caption(text: str, width: int = 42) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def to_srt(cues: Iterable[tuple[float, float, str]]) -> str:
    out = []
    for i, (start, end, text) in enumerate((c for c in cues if c[2].strip()), 1):
        out.append(f"{i}\n{_ts(start)} --> {_ts(end)}\n{wrap_caption(text.strip())}\n")
    return "\n".join(out)


def to_vtt(cues: Iterable[tuple[float, float, str]]) -> str:
    body = [f"{_ts(s, '.')} --> {_ts(e, '.')}\n{wrap_caption(t.strip())}\n" for s, e, t in cues if t.strip()]
    return "WEBVTT\n\n" + "\n".join(body)
