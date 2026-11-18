"""Pure text-chunking helpers: sentence splitting, size packing, Markdown blocks and tables, digit handling.

No I/O and no model calls, so every limit-handling rule here is unit-tested directly.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

# Sentence terminators across the scripts we handle: Latin . ! ?, Devanagari danda । and double danda ॥,
# Urdu full stop ۔ and Arabic question mark ؟. Most Indic scripts use "." too.
_SENTENCE_RE = re.compile(r"(?<=[.!?।॥۔؟])\s+|(?<=[.!?।॥۔؟][\"'”’)\]])\s+|\n+")
_SOFT_BREAK_RE = re.compile(r"(?<=[,;:،])\s+")


def split_sentences(text: str) -> list[str]:
    """Split on sentence terminators and newlines, keeping the punctuation with its sentence."""
    return [s.strip() for s in _SENTENCE_RE.split(text) if s and s.strip()]


def _hard_split(piece: str, max_chars: int) -> list[str]:
    """Split an over-long sentence at commas, then at spaces, then (last resort) mid-word."""
    out: list[str] = []
    for part in _SOFT_BREAK_RE.split(piece) if len(piece) > max_chars else [piece]:
        while len(part) > max_chars:
            cut = part.rfind(" ", 0, max_chars + 1)
            cut = cut if cut > max_chars // 3 else max_chars
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            out.append(part)
    return _greedy(out, max_chars, sep=" ")


def _greedy(pieces: list[str], max_chars: int, sep: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for p in pieces:
        candidate = f"{current}{sep}{p}" if current else p
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = p
    if current:
        chunks.append(current)
    return chunks


def pack_sentences(text: str, max_chars: int) -> list[str]:
    """Group sentences into chunks of at most ``max_chars`` without breaking sentences when avoidable.

    Joining the chunks with spaces reproduces every word of the input, in order.
    """
    if max_chars < 10:
        raise ValueError("max_chars must be at least 10")
    pieces: list[str] = []
    for s in split_sentences(text):
        pieces.extend(_hard_split(s, max_chars) if len(s) > max_chars else [s])
    return _greedy(pieces, max_chars, sep=" ")


def pack_paragraphs(paragraphs: list[str], max_chars: int) -> list[list[int]]:
    """Group paragraph indexes so each group's joined text stays within ``max_chars``.

    A paragraph longer than the limit gets a group of its own; the caller splits it with pack_sentences.
    """
    groups: list[list[int]] = []
    size = 0
    for i, p in enumerate(paragraphs):
        n = len(p) + 2
        if groups and size + n <= max_chars and len(p) <= max_chars:
            groups[-1].append(i)
            size += n
        else:
            groups.append([i])
            size = n
    return groups


def _word_key(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower())


def stitch_overlap(previous: str, following: str, max_words: int = 8) -> str:
    """Drop words at the start of ``following`` that repeat the end of ``previous``.

    Used after a hard cut, where consecutive audio windows overlap by a fraction of a second.
    """
    prev_words, next_words = previous.split(), following.split()
    for k in range(min(max_words, len(prev_words), len(next_words)), 0, -1):
        if [_word_key(w) for w in prev_words[-k:]] == [_word_key(w) for w in next_words[:k]]:
            return " ".join(next_words[k:])
    return following


# --------------------------------------------------------------------------- digits and numbers


def normalize_digits(text: str) -> str:
    """Map every Unicode decimal digit (Devanagari ४, Tamil ௫, Bengali ৫, ...) to ASCII."""
    return "".join(str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in text)


_NUMBER_RE = re.compile(r"\d+(?:[.,:/-]\d+)*")


def extract_numbers(text: str) -> Counter[str]:
    return Counter(_NUMBER_RE.findall(normalize_digits(text)))


def missing_numbers(source: str, translation: str) -> list[str]:
    """Numbers present in the source but absent from the translation (digit scripts normalised)."""
    src, dst = extract_numbers(source), extract_numbers(translation)
    return sorted((src - dst).elements())


# --------------------------------------------------------------------------- Markdown


_HEADING_RE = re.compile(r"^(\s{0,3}#{1,6}\s+)(.*)$")
_LIST_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$")
_QUOTE_RE = re.compile(r"^(\s*>\s?)(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_COMMENT_RE = re.compile(r"^\s*<!--.*-->\s*$")
_NON_TEXT_CELL_RE = re.compile(r"^[\s\d.,:;/%₹$+\-–—()*_~`'\"|]*$")
_URL_RE = re.compile(r"^\s*(https?://|www\.)\S+\s*$")


@dataclass
class Block:
    """One Markdown block. ``prefix`` (e.g. "## " or "- ") is kept verbatim; ``text`` is translatable."""

    kind: str  # paragraph | heading | list | quote | table | code | hr | comment | blank
    prefix: str = ""
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)  # tables: cell text per row
    sep_index: int | None = None  # tables: index of the |---| row
    sep_line: str = ""

    @property
    def translatable(self) -> bool:
        return self.kind in {"paragraph", "heading", "list", "quote"} and bool(self.text.strip())


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def is_translatable_cell(cell: str) -> bool:
    return bool(cell.strip()) and not _NON_TEXT_CELL_RE.match(cell) and not _URL_RE.match(cell)


def parse_markdown(text: str) -> list[Block]:
    lines = text.splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE_RE.match(line):
            fence = _FENCE_RE.match(line).group(1)
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            blocks.append(Block("code", text="\n".join(lines[i : j + 1])))
            i = j + 1
            continue
        if not line.strip():
            blocks.append(Block("blank"))
            i += 1
            continue
        if _COMMENT_RE.match(line):
            blocks.append(Block("comment", text=line))
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            rows = [split_row(line)]
            sep_line = lines[i + 1]
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(split_row(lines[j]))
                j += 1
            blocks.append(Block("table", rows=rows, sep_index=1, sep_line=sep_line))
            i = j
            continue
        if _HR_RE.match(line):
            blocks.append(Block("hr", text=line))
        elif m := _HEADING_RE.match(line):
            blocks.append(Block("heading", prefix=m.group(1), text=m.group(2)))
        elif m := _LIST_RE.match(line):
            blocks.append(Block("list", prefix=m.group(1), text=m.group(2)))
        elif m := _QUOTE_RE.match(line):
            blocks.append(Block("quote", prefix=m.group(1), text=m.group(2)))
        else:
            # Consecutive plain lines form one paragraph (soft-wrapped text).
            j = i + 1
            para = [line.strip()]
            while j < len(lines) and lines[j].strip() and not _starts_block(lines, j):
                para.append(lines[j].strip())
                j += 1
            blocks.append(Block("paragraph", text=" ".join(para)))
            i = j
            continue
        i += 1
    return blocks


def _starts_block(lines: list[str], j: int) -> bool:
    line = lines[j]
    return bool(
        _HEADING_RE.match(line) or _LIST_RE.match(line) or _QUOTE_RE.match(line) or _FENCE_RE.match(line)
        or _HR_RE.match(line) or _COMMENT_RE.match(line)
        or ("|" in line and j + 1 < len(lines) and _TABLE_SEP_RE.match(lines[j + 1]))
    )


def render_markdown(blocks: list[Block]) -> str:
    out: list[str] = []
    for b in blocks:
        if b.kind == "blank":
            out.append("")
        elif b.kind == "table":
            width = max(len(r) for r in b.rows)
            for idx, row in enumerate(b.rows):
                cells = row + [""] * (width - len(row))
                out.append("| " + " | ".join(cells) + " |")
                if idx == 0:
                    out.append(b.sep_line)
        elif b.kind in {"code", "hr", "comment"}:
            out.append(b.text)
        else:
            out.append(f"{b.prefix}{b.text}")
    return "\n".join(out)


def table_shape(text: str) -> list[tuple[int, int]]:
    """(rows, columns) for every table in a Markdown document; used to verify tables survived."""
    return [(len(b.rows), max(len(r) for r in b.rows)) for b in parse_markdown(text) if b.kind == "table"]
