"""Plain-code text helpers: segmentation, Markdown tables, numbers, similarity.

These are deliberately deterministic: anything that can be checked by code is
checked by code rather than by the LLM.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

# Sentence terminators across Indic scripts: . ? ! plus danda (।), double danda (॥),
# Urdu full stop (۔) and the Ol Chiki mucaad (᱾).
_SENT_END = re.compile(r"(?<=[.?!।॥۔᱾])\s+|\n+")
_SOFT_BREAK = re.compile(r"(?<=[,;:،])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_END.split(text) if s and s.strip()]


def pack_segments(text: str, max_chars: int) -> list[str]:
    """Split text into speakable segments of at most `max_chars`, cutting at sentence,
    then clause, then word boundaries. Never drops text."""
    out: list[str] = []
    buf = ""
    for sent in split_sentences(text):
        pieces = [sent] if len(sent) <= max_chars else _split_long(sent, max_chars)
        for piece in pieces:
            if not buf:
                buf = piece
            elif len(buf) + 1 + len(piece) <= max_chars:
                buf = f"{buf} {piece}"
            else:
                out.append(buf)
                buf = piece
    if buf:
        out.append(buf)
    return out


def _split_long(sentence: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    for clause in _SOFT_BREAK.split(sentence):
        if len(clause) <= max_chars:
            parts.append(clause)
            continue
        words, cur = clause.split(), ""
        for w in words:
            while len(w) > max_chars:  # a pathological unbroken token
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.append(w[:max_chars])
                w = w[max_chars:]
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= max_chars:
                cur = f"{cur} {w}"
            else:
                parts.append(cur)
                cur = w
        if cur:
            parts.append(cur)
    # re-pack small clauses together
    packed: list[str] = []
    for p in parts:
        if packed and len(packed[-1]) + 1 + len(p) <= max_chars:
            packed[-1] = f"{packed[-1]} {p}"
        else:
            packed.append(p)
    return packed


# ---------------------------------------------------------------------------
# Markdown block structure (tables must survive translation)
# ---------------------------------------------------------------------------

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HTML_TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)


@dataclass
class Block:
    kind: str  # "text" | "table" | "blank" | "code"
    text: str


def split_markdown_blocks(md: str) -> list[Block]:
    """Group Markdown into paragraphs, pipe tables and fenced code; keeps exact text."""
    blocks: list[Block] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            blocks.append(Block("code", "\n".join(lines[i : j + 1])))
            i = j + 1
            continue
        if _TABLE_ROW.match(line):
            j = i
            while j < len(lines) and _TABLE_ROW.match(lines[j]):
                j += 1
            blocks.append(Block("table", "\n".join(lines[i:j])))
            i = j
            continue
        if not line.strip():
            blocks.append(Block("blank", ""))
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip() and not _TABLE_ROW.match(lines[j]) and not lines[j].strip().startswith("```"):
            j += 1
        blocks.append(Block("text", "\n".join(lines[i:j])))
        i = j
    return blocks


def parse_table(table_md: str) -> list[list[str]]:
    rows = []
    for line in table_md.splitlines():
        if _TABLE_SEP.match(line):
            continue
        cells = line.strip().strip("|").split("|")
        rows.append([c.strip() for c in cells])
    return rows


def table_shapes(md: str) -> list[tuple[int, int]]:
    """(rows, max columns) for each pipe table, plus HTML tables counted by <tr>."""
    shapes = []
    for b in split_markdown_blocks(md):
        if b.kind == "table":
            rows = parse_table(b.text)
            shapes.append((len(rows), max((len(r) for r in rows), default=0)))
    for m in _HTML_TABLE.finditer(md):
        rows = re.findall(r"<tr\b", m.group(0), re.I)
        cols = max((len(re.findall(r"<t[dh]\b", r, re.I)) for r in re.split(r"<tr\b", m.group(0), flags=re.I)[1:]), default=0)
        shapes.append((len(rows), cols))
    return shapes


def render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(norm[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Numbers, normalisation and similarity
# ---------------------------------------------------------------------------

def to_ascii_digits(text: str) -> str:
    out = []
    for ch in text:
        if ch.isdigit() and not ("0" <= ch <= "9"):
            try:
                out.append(str(unicodedata.decimal(ch)))
                continue
            except ValueError:
                pass
        out.append(ch)
    return "".join(out)


_NUM = re.compile(r"\d+(?:[.,:/]\d+)*")


def extract_numbers(text: str) -> list[str]:
    return [n.replace(",", "") for n in _NUM.findall(to_ascii_digits(text))]


def number_check(source: str, target: str) -> dict:
    src, tgt = Counter(extract_numbers(source)), Counter(extract_numbers(target))
    missing = sorted((src - tgt).elements())
    extra = sorted((tgt - src).elements())
    return {"source_numbers": sum(src.values()), "missing": missing[:20], "unexpected": extra[:20],
            "preserved_ratio": round(1 - len(missing) / max(sum(src.values()), 1), 3)}


def letters_only(text: str) -> str:
    """Lower-case letters and combining marks only (drops digits, punctuation, spaces)."""
    return "".join(ch for ch in unicodedata.normalize("NFC", text).lower()
                   if ch.isalpha() or unicodedata.category(ch).startswith("M"))


def similarity(a: str, b: str) -> float:
    """Character-level similarity in [0,1] between letter-only forms (1 - normalized edit-ish distance)."""
    x, y = letters_only(a), letters_only(b)
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    return SequenceMatcher(None, x, y, autojunk=False).ratio()


def chrf(hypothesis: str, reference: str, n: int = 3) -> float:
    """Character n-gram F-score (chrF-like, beta=2) on whitespace-stripped lower text."""
    h, r = re.sub(r"\s+", " ", hypothesis.lower()).strip(), re.sub(r"\s+", " ", reference.lower()).strip()
    if not h or not r:
        return 0.0
    precisions, recalls = [], []
    for k in range(1, n + 1):
        hg = Counter(h[i : i + k] for i in range(len(h) - k + 1))
        rg = Counter(r[i : i + k] for i in range(len(r) - k + 1))
        overlap = sum((hg & rg).values())
        precisions.append(overlap / max(sum(hg.values()), 1))
        recalls.append(overlap / max(sum(rg.values()), 1))
    p, rc = sum(precisions) / n, sum(recalls) / n
    if p + rc == 0:
        return 0.0
    beta2 = 4.0
    return (1 + beta2) * p * rc / (beta2 * p + rc)


def repetition_score(text: str, n: int = 4) -> float:
    """Share of word n-grams that occur more than once (0 = no repetition)."""
    words = text.split()
    if len(words) < n * 2:
        return 0.0
    grams = Counter(tuple(words[i : i + n]) for i in range(len(words) - n + 1))
    repeated = sum(c for c in grams.values() if c > 1)
    return repeated / max(sum(grams.values()), 1)


# ---------------------------------------------------------------------------
# Untrusted content
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = re.compile(
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts?)|disregard (the|all|your) (instructions|rules)"
    r"|you are now|system prompt|new instructions?:|developer mode|act as (an?|the) |do not translate"
    r"|call the (tool|function)|write (the|a) file|delete (all|the) files?|send (the|your) (api )?key"
    r"|मान्य निर्देश|पिछले निर्देश|निर्देशों को अनदेखा",
    re.I,
)


def injection_signals(text: str) -> list[str]:
    """Heuristic flags for instruction-like content inside data. Advisory only: the real
    defence is that extracted content is never treated as instructions."""
    return sorted({m.group(0).lower() for m in _INJECTION_PATTERNS.finditer(text)})[:10]


def preview(text: str, limit: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + f" … [{len(text) - limit} more chars]"
