"""Tokens, words and sentences with character offsets into the original text."""

from __future__ import annotations

from dataclasses import dataclass

import regex

WORD_RE = regex.compile(r"[\p{L}\p{M}\p{N}]+(?:['’\-][\p{L}\p{M}\p{N}]+)*")
TOKEN_RE = regex.compile(r"\S+")
SENTENCE_END = regex.compile(r"(?<=[.!?।॥۔؟᱾꯫])[\"'”’)\]]*(?=\s|$)")
ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "e.g", "i.e", "jr", "sr", "no", "fig", "u.s", "inc", "ltd", "mt", "approx", "dept"}

EN_STOPWORDS = frozenset(
    """a an the and or but if then so of to in on at by for with from as is are was were be been being am do does did
    this that these those it its it's he she they them his her their we us our you your i me my mine not no nor
    there here which who whom whose what when where why how all any both each few more most other some such only own same
    than too very can will just should would could may might must shall into over under about after before between through
    during above below up down out off again further once also has have had having said says""".split()
)


@dataclass(frozen=True)
class Piece:
    text: str
    start: int
    end: int


def tokens(text: str) -> list[Piece]:
    return [Piece(m.group(0), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]


def words(text: str) -> list[Piece]:
    return [Piece(m.group(0), m.start(), m.end()) for m in WORD_RE.finditer(text)]


def _is_abbreviation(text: str, end: int) -> bool:
    start = end - 1
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    word = text[start:end].rstrip(".").lower().lstrip("(\"'")
    if word in ABBREVIATIONS:
        return True
    return len(word) == 1 and word.isalpha() and word.isascii()


def sentences(text: str) -> list[Piece]:
    """Split on sentence terminators and newlines; offsets cover each sentence without outer whitespace."""
    pieces: list[Piece] = []
    for line_match in regex.finditer(r"[^\n]+", text):
        line, base = line_match.group(0), line_match.start()
        cursor = 0
        for m in SENTENCE_END.finditer(line):
            end = m.end()
            if line[m.start() - 1] == "." and _is_abbreviation(line, m.start()):
                continue
            _append(pieces, line, base, cursor, end)
            cursor = end
        _append(pieces, line, base, cursor, len(line))
    return pieces


def _append(pieces: list[Piece], line: str, base: int, start: int, end: int) -> None:
    chunk = line[start:end]
    stripped = chunk.strip()
    if not stripped:
        return
    lead = len(chunk) - len(chunk.lstrip())
    s = base + start + lead
    pieces.append(Piece(stripped, s, s + len(stripped)))


def clauses(text: str) -> list[Piece]:
    """Sentences further split at commas, semicolons and colons."""
    out: list[Piece] = []
    for sent in sentences(text):
        cursor = 0
        for m in regex.finditer(r"[,;:،](?=\s)", sent.text):
            _append(out, sent.text, sent.start, cursor, m.end())
            cursor = m.end()
        _append(out, sent.text, sent.start, cursor, len(sent.text))
    return out


def stem(word: str) -> str:
    """A tiny English suffix stripper; enough to match 'cells' with 'cell' across round trips."""
    w = word.lower().replace("’", "'")
    if w.endswith("'s"):
        w = w[:-2]
    for suffix in ("ational", "ization", "ations", "ation", "ments", "ment", "ness", "ingly", "edly", "ing", "ies", "ied", "ed", "es", "ly", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[: -len(suffix)]
            if suffix in {"ies", "ied"}:
                w += "y"
            break
    return w


def content_words(text: str) -> list[Piece]:
    return [w for w in words(text) if w.text.lower() not in EN_STOPWORDS and not w.text.isdigit() and len(w.text) > 1]
