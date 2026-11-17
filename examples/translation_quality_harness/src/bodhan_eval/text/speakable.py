"""Turn a (possibly Markdown/LaTeX) translation into the text a TTS model should speak.

The result keeps a per-character map back into the translation so speech flags can point at
translation spans.
"""

from __future__ import annotations

from dataclasses import dataclass

import regex

from .markup import HEADING, LINK, LIST_ITEM, TABLE_SEP, lines, math_segments


@dataclass(frozen=True)
class Speakable:
    text: str
    offsets: tuple[int, ...]

    def span_to_translation(self, start: int, end: int) -> tuple[int, int]:
        if not self.offsets:
            return (0, 0)
        start = min(max(start, 0), len(self.offsets) - 1)
        end = min(max(end, start + 1), len(self.offsets))
        return (self.offsets[start], self.offsets[end - 1] + 1)


def to_speakable(text: str) -> Speakable:
    drop = bytearray(len(text))
    replace: dict[int, str] = {}

    def mark(s: int, e: int) -> None:
        for k in range(max(s, 0), min(e, len(text))):
            drop[k] = 1

    for seg in math_segments(text):
        mark(seg.start, seg.end)
    for ln in lines(text):
        if TABLE_SEP.match(ln.text) and "|" in ln.text:
            mark(ln.start, ln.end)
            continue
        for rx in (HEADING, LIST_ITEM):
            m = rx.match(ln.text)
            if m:
                mark(ln.start, ln.start + m.end())
        m = regex.match(r"^\s*>\s?", ln.text)
        if m:
            mark(ln.start, ln.start + m.end())
        if ln.text.strip().startswith(("```", "~~~")):
            mark(ln.start, ln.end)
        if not regex.search(r"[.!?।॥۔:,;]\s*$", ln.text):
            replace[ln.end - 1] = text[ln.end - 1] + "."
    for m in LINK.finditer(text):
        close = text.find("](", m.start())
        mark(m.start(), m.start() + (2 if text[m.start()] == "!" else 1))
        mark(close, m.end())
    for m in regex.finditer(r"\*\*|__|`|~~", text):
        mark(m.start(), m.end())
    for m in regex.finditer(r"(?<![\p{L}\p{M}\p{N}])[#¶]+(?![\p{L}\p{M}\p{N}])", text):
        mark(m.start(), m.end())
    for m in regex.finditer(r"(?<!\\)\|", text):
        if not drop[m.start()]:
            replace[m.start()] = ","

    out_chars: list[str] = []
    offsets: list[int] = []
    for i, ch in enumerate(text):
        if drop[i]:
            continue
        piece = replace.get(i, ch)
        for c in piece:
            if c.isspace():
                c = " "
            if c == " " and (not out_chars or out_chars[-1] == " "):
                continue
            if c == "," and out_chars and out_chars[-1] in ".!?।":
                continue
            if c == "," and out_chars and out_chars[-1] in ", ":
                while out_chars and out_chars[-1] == " ":
                    out_chars.pop()
                    offsets.pop()
                if not out_chars or out_chars[-1] in ",.!?।":
                    if out_chars:
                        out_chars.append(" ")
                        offsets.append(i)
                    continue
            out_chars.append(c)
            offsets.append(i)
    while out_chars and out_chars[-1] in " ,":
        out_chars.pop()
        offsets.pop()
    while out_chars and out_chars[0] in " ,.":
        out_chars.pop(0)
        offsets.pop(0)
    return Speakable("".join(out_chars), tuple(offsets))


def chunk_for_tts(sp: Speakable, max_chars: int = 220) -> list[tuple[int, int]]:
    """Split speakable text into sentence-sized (start, end) chunks of at most ~max_chars."""
    text = sp.text
    bounds: list[tuple[int, int]] = []
    for m in regex.finditer(r"[^.!?।॥۔]+[.!?।॥۔]*", text):
        s, e = m.start(), m.end()
        while s < e and text[s] == " ":
            s += 1
        if s >= e:
            continue
        if bounds and (e - bounds[-1][0]) <= max_chars // 2:
            bounds[-1] = (bounds[-1][0], e)
            continue
        while e - s > max_chars:
            cut = text.rfind(" ", s, s + max_chars)
            cut = cut if cut > s else s + max_chars
            bounds.append((s, cut))
            s = cut + 1 if cut < len(text) and text[cut] == " " else cut
        if s < e:
            bounds.append((s, e))
    return bounds
