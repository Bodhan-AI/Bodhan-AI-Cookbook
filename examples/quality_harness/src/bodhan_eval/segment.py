"""Sentence splitting, and the speakable form of a text (what Indic-Speak is asked to read).

Language-independent: sentence ends are the terminators used by every script we support.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# A sentence ends at a terminator followed by whitespace, or at a line break.
# "Dr. Rao" splits after "Dr." on purpose: no abbreviation lists per language. The aligner's 2-1 and
# 1-2 moves join the halves again, and for speech it only adds a pause.
_SENTENCE_END = re.compile(r"(?<=[.?!।॥؟۔])\s+|\n+")

_FENCE = re.compile(r"^ {0,3}```.*?(?:^ {0,3}```[^\n]*$|\Z)", re.S | re.M)
_MATH = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|\$[^$\s](?:[^$\n]*[^$\s])?\$(?!\d)", re.S)
_DISPLAY_MATH = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]", re.S)
_LINK = re.compile(r"!?\[([^\]\n]*)\]\([^)\n]*\)")  # links and images -> their text
_URL = re.compile(r"https?://\S+")
_HTML = re.compile(r"<[^>\n]+>")
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", re.M)
_TABLE_EDGE = re.compile(r"^[ \t]*\||\|[ \t]*$", re.M)
_LINE_MARK = re.compile(r"^ {0,3}(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+[.)][ \t]+|>[ \t]?)", re.M)
_INLINE = re.compile(r"\*\*|__|~~|`|(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """[start, end) of each non-empty sentence, trimmed, in order."""
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if s < e:
            out.append((s, e))
    return out


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    text: str


_MARKUP_PREFIX = re.compile(r"(?:#{1,6}|[-*+]|\d+[.)]|>)[ \t]+")


def segments(text: str) -> list[Segment]:
    """Translatable sentences with their offsets: Markdown blocks split into sentences, table rows kept
    whole, line markup ("## ", "- ", "| ") trimmed off, and code blocks, display math and table rules left
    out (the format check covers those). Segments without a letter ("1.", "42 %") are dropped.
    """
    blanked = text
    for pattern in (_FENCE, _DISPLAY_MATH, _TABLE_RULE):  # blank out, keeping every offset
        blanked = pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), blanked)
    out = []
    for s, e in split_sentences(blanked):
        if m := _MARKUP_PREFIX.match(blanked, s, e):
            s = m.end()
        while s < e and blanked[s] in "| \t":
            s += 1
        while e > s and blanked[e - 1] in "| \t":
            e -= 1
        if any(unicodedata.category(c).startswith("L") for c in blanked[s:e]):
            out.append(Segment(s, e, text[s:e]))
    return out


def speakable(text: str) -> str:
    """Text with Markdown syntax, code blocks, math, URLs and HTML removed: only what can be read aloud.

    Table rows become lines with cells separated by commas, so each row is spoken as one sentence.
    """
    text = _FENCE.sub("\n", text)
    text = _MATH.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _HTML.sub(" ", text)
    text = _TABLE_RULE.sub("", text)
    text = re.sub(r"[ \t]*\|[ \t]*", ", ", _TABLE_EDGE.sub("", text))
    text = _LINE_MARK.sub("", text)
    text = _INLINE.sub("", text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line.strip(" ,"))


def chunks(text: str, max_chars: int) -> list[str]:
    """Sentences, with any sentence longer than max_chars cut at a comma or space before the limit."""
    out = []
    for s, e in split_sentences(text):
        sentence = text[s:e]
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(", ", 0, max_chars) + 1, sentence.rfind(" ", 0, max_chars))
            if cut <= 0:
                cut = max_chars
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            out.append(sentence)
    return out
