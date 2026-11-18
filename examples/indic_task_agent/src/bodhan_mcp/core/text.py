"""Text windowing shared by the tools: sentence packing, document splitting, page specs."""
from __future__ import annotations

import re

SENTENCE_END = re.compile(r"(?<=[।॥.!?])\s+")
HEADING = re.compile(r"(?m)^(#{1,6}\s)")


def pack_sentences(text: str, limit: int) -> list[str]:
    """Greedy-pack sentences into pieces of at most `limit` chars; never split a sentence unless one alone is too long."""
    text = re.sub(r"[ \t]+", " ", text.strip())
    if not text:
        return []
    sentences = [s.strip() for s in SENTENCE_END.split(text) if s.strip()]
    out: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > limit:
            head, s = _split_long(s, limit)
            if cur:
                out.append(cur)
                cur = ""
            out.append(head)
        if not cur:
            cur = s
        elif len(cur) + 1 + len(s) <= limit:
            cur = f"{cur} {s}"
        else:
            out.append(cur)
            cur = s
    if cur:
        out.append(cur)
    return out


def _split_long(text: str, limit: int) -> tuple[str, str]:
    cut = text.rfind(" ", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].strip(), text[cut:].strip()


def split_document(text: str, limit: int) -> list[str]:
    """Split a document into pieces <= limit at headings/page breaks, never mid-paragraph."""
    if len(text) <= limit:
        return [text] if text.strip() else []
    blocks = re.split(r"(\n\s*---\s*\n|\n{2,})", text)
    pieces: list[str] = []
    cur = ""
    for block in blocks:
        if not block:
            continue
        if len(cur) + len(block) <= limit:
            cur += block
            continue
        if cur.strip():
            pieces.append(cur.strip("\n"))
        if len(block) <= limit:
            cur = block
            continue
        # a single oversized block: split at headings, then paragraphs, then sentences
        for sub in _hard_split(block, limit):
            if pieces and len(pieces[-1]) + len(sub) <= limit:
                pieces[-1] += sub
            else:
                pieces.append(sub)
        cur = ""
    if cur.strip():
        pieces.append(cur.strip("\n"))
    return [p for p in pieces if p.strip()]


def _hard_split(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    parts = HEADING.split(block)
    if len(parts) > 1:
        chunks, cur = [], ""
        for i in range(0, len(parts), 2):
            piece = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
            if len(cur) + len(piece) <= limit:
                cur += piece
            else:
                if cur:
                    chunks.append(cur)
                cur = piece
        if cur:
            chunks.append(cur)
        if all(len(c) <= limit for c in chunks):
            return chunks
    out: list[str] = []
    rest = block
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest.strip():
        out.append(rest)
    return out


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    """Turn "1-4", "2,5", "3" into 0-based indices. Empty spec means every page."""
    if not spec:
        return list(range(page_count))
    chosen: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            chosen.update(range(int(lo) - 1, int(hi)))
        else:
            chosen.add(int(part) - 1)
    return sorted(p for p in chosen if 0 <= p < page_count)


def digits(text: str) -> list[str]:
    """Every digit in the text, with Indic digits folded to ASCII, for numeric-preservation checks."""
    table = {c: str(i) for base in (0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66) for i, c in enumerate(range(base, base + 10))}
    return [ch for ch in text.translate(table) if ch.isdigit()]
