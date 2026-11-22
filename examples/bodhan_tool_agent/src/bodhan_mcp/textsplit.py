"""Sentence and block splitting that respects Markdown tables, LaTeX and code fences."""
from __future__ import annotations

import re

_SENT_END = re.compile(r"(?<=[.!?।॥۔])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_END.split(text) if s and s.strip()]


def _hard_split(s: str, max_chars: int) -> list[str]:
    """Split one over-long sentence at clause marks, then at spaces."""
    if len(s) <= max_chars:
        return [s]
    parts, cur = [], ""
    for piece in re.split(r"(?<=[,;:،、])\s+", s):
        if cur and len(cur) + 1 + len(piece) > max_chars:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        parts.append(cur)
    out: list[str] = []
    for p in parts:
        while len(p) > max_chars:
            cut = p.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def chunk_for_tts(text: str, max_chars: int) -> list[str]:
    """Pack whole sentences into chunks <= max_chars so no sentence is cut mid-way (unless it must be)."""
    chunks: list[str] = []
    cur = ""
    for sent in split_sentences(text):
        for piece in _hard_split(sent, max_chars):
            if cur and len(cur) + 1 + len(piece) > max_chars:
                chunks.append(cur)
                cur = piece
            else:
                cur = f"{cur} {piece}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def split_blocks(text: str) -> list[str]:
    """Split on blank lines, but keep tables, display math and code fences whole."""
    blocks: list[list[str]] = [[]]
    in_fence = in_math = False
    html_depth = 0
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        if not in_fence and stripped.count("$$") % 2 == 1:
            in_math = not in_math
        html_depth += len(re.findall(r"<table\b", line, re.I)) - len(re.findall(r"</table>", line, re.I))
        keep_open = in_fence or in_math or html_depth > 0
        if stripped == "" and not keep_open:
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line)
    return ["\n".join(b) for b in blocks if b]


def _is_atomic(block: str) -> bool:
    s = block.lstrip()
    return s.startswith(("|", "<table", "```", "$$"))


def chunk_blocks(text: str, max_chars: int) -> list[str]:
    """Pack blocks into chunks of about max_chars. Atomic blocks (tables/math/code) are never split."""
    chunks: list[str] = []
    cur: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal cur, size
        if cur:
            chunks.append("\n\n".join(cur))
        cur, size = [], 0

    for b in split_blocks(text):
        pieces = [b] if (_is_atomic(b) or len(b) <= max_chars) else _hard_split_block(b, max_chars)
        for p in pieces:
            if cur and size + len(p) + 2 > max_chars:
                flush()
            cur.append(p)
            size += len(p) + 2
    flush()
    return chunks


def _hard_split_block(b: str, max_chars: int) -> list[str]:
    out, cur = [], ""
    for s in split_sentences(b):
        for piece in _hard_split(s, max_chars):
            if cur and len(cur) + 1 + len(piece) > max_chars:
                out.append(cur)
                cur = piece
            else:
                cur = f"{cur} {piece}".strip()
    if cur:
        out.append(cur)
    return out


def split_in_half(chunk: str) -> list[str]:
    """Used when a translation comes back truncated: retry with two smaller inputs."""
    blocks = split_blocks(chunk)
    if len(blocks) >= 2:
        mid = len(blocks) // 2
        return ["\n\n".join(blocks[:mid]), "\n\n".join(blocks[mid:])]
    sents = split_sentences(chunk)
    if len(sents) >= 2:
        mid = len(sents) // 2
        return [" ".join(sents[:mid]), " ".join(sents[mid:])]
    cut = chunk.rfind(" ", 0, len(chunk) // 2 + 1)
    cut = cut if cut > 0 else len(chunk) // 2
    return [chunk[:cut], chunk[cut:]] if cut > 0 else [chunk]
