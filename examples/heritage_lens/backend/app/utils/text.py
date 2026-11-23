"""Sentence-aware text chunking (stdlib only). Nothing is ever dropped."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Latin . ! ? … ; Devanagari danda/double danda; Urdu full stop and question mark.
_TERMINATORS = ".!?\u2026\u0964\u0965\u06d4\u061f"
_SENT_SPLIT = re.compile(rf"(?<=[{re.escape(_TERMINATORS)}])(\s+)")
_PARA_SPLIT = re.compile(r"(\n[ \t]*\n+)")
_WS_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    separator: str  # the original whitespace that followed this chunk


def _break_long(sentence: str, ws: str, max_chars: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    s = sentence
    while len(s) > max_chars:
        window = s[: max_chars + 1]
        idx = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
        if idx < max_chars * 0.3:  # no sensible whitespace: hard cut
            out.append((s[:max_chars], ""))
            s = s[max_chars:]
        else:
            run = _WS_RUN.match(s, idx)
            sep = run.group() if run else " "
            out.append((s[:idx], sep))
            s = s[idx + len(sep):]
    out.append((s, ws))
    return out


def split_text(text: str, max_chars: int) -> list[TextChunk]:
    """Split into chunks of <= max_chars at paragraph, then sentence, then word boundaries.

    ``"".join(c.text + c.separator for c in chunks) == text.strip()`` always holds.
    """
    if max_chars < 20:
        raise ValueError("max_chars must be at least 20")
    text = text.strip()
    if not text:
        return []
    parts = _PARA_SPLIT.split(text)
    chunks: list[TextChunk] = []
    for i in range(0, len(parts), 2):
        para = parts[i]
        para_sep = parts[i + 1] if i + 1 < len(parts) else ""
        if not para.strip():
            if chunks and para_sep:
                last = chunks[-1]
                chunks[-1] = TextChunk(last.index, last.text, last.separator + para_sep)
            continue
        pieces = _SENT_SPLIT.split(para)
        atoms: list[tuple[str, str]] = []
        for k in range(0, len(pieces), 2):
            ws = pieces[k + 1] if k + 1 < len(pieces) else ""
            atoms.extend(_break_long(pieces[k], ws, max_chars))
        cur_text, cur_sep = "", ""
        started = False
        for t, sep in atoms:
            if not started:
                cur_text, cur_sep, started = t, sep, True
            elif len(cur_text) + len(cur_sep) + len(t) <= max_chars:
                cur_text, cur_sep = cur_text + cur_sep + t, sep
            else:
                chunks.append(TextChunk(len(chunks), cur_text, cur_sep))
                cur_text, cur_sep = t, sep
        if started:
            chunks.append(TextChunk(len(chunks), cur_text, para_sep or cur_sep))
    return chunks


def join_chunks(chunks: list[TextChunk]) -> str:
    return "".join(c.text + c.separator for c in chunks)


def split_in_two(text: str) -> tuple[str, str] | None:
    """Split text near its middle at a sentence or word boundary (for truncation retries)."""
    text = text.strip()
    if len(text) < 40:
        return None
    mid = len(text) // 2
    best = None
    for m in _SENT_SPLIT.finditer(text):
        if best is None or abs(m.start() - mid) < abs(best.start() - mid):
            best = m
    if best is None:
        for m in _WS_RUN.finditer(text):
            if best is None or abs(m.start() - mid) < abs(best.start() - mid):
                best = m
    if best is None:
        return None
    left, right = text[: best.start()], text[best.end():]
    return (left, right) if left.strip() and right.strip() else None
