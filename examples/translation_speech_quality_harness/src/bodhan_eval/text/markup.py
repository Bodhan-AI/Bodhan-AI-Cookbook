"""Markdown / LaTeX structure, and turning marked-up text into something speakable."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import regex

MATH_BLOCK = regex.compile(r"\$\$(.+?)\$\$", regex.S)
MATH_INLINE = regex.compile(r"(?<![\\$])\$(?!\$)(.+?)(?<![\\$])\$(?!\$)")
MATH_PAREN = regex.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]", regex.S)
LATEX_CMD = regex.compile(r"\\[A-Za-z]+")
LATEX_TEXT_ARG = regex.compile(r"\\(?:text|mathrm|textbf|textit|operatorname)\{[^{}]*\}")
ENV = regex.compile(r"\\(begin|end)\{([A-Za-z*]+)\}")
LINK = regex.compile(r"!?\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
URL = regex.compile(r"https?://\S+")
CODE_FENCE = regex.compile(r"^\s*```", regex.M)
INLINE_CODE = regex.compile(r"(?<!`)`([^`\n]+)`(?!`)")
HEADING = regex.compile(r"^(#{1,6})\s", regex.M)
LIST_ITEM = regex.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", regex.M)
TABLE_ROW = regex.compile(r"^\s*\|.*\|\s*$", regex.M)
TABLE_SEP = regex.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
BOLD = regex.compile(r"\*\*|__")


def has_markup(text: str) -> bool:
    return bool(MATH_BLOCK.search(text) or MATH_INLINE.search(text) or MATH_PAREN.search(text) or HEADING.search(text)
                or TABLE_ROW.search(text) or CODE_FENCE.search(text) or LINK.search(text) or BOLD.search(text) or INLINE_CODE.search(text)
                or len(LIST_ITEM.findall(text)) >= 2)


def math_segments(text: str) -> list[tuple[int, int, str]]:
    segs = []
    taken: list[tuple[int, int]] = []
    for rx in (MATH_BLOCK, MATH_PAREN, MATH_INLINE):
        for m in rx.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            body = next(g for g in m.groups() if g is not None)
            segs.append((m.start(), m.end(), body))
            taken.append((m.start(), m.end()))
    return sorted(segs)


def math_fingerprint(body: str) -> str:
    """Math should be copied verbatim; ignore whitespace and translatable \\text{} contents."""
    body = LATEX_TEXT_ARG.sub(r"\\text{}", body)
    return regex.sub(r"\s+", "", body)


@dataclass
class Structure:
    headings: Counter = field(default_factory=Counter)
    list_items: int = 0
    table_rows: list[int] = field(default_factory=list)  # cells per row (separator rows excluded)
    table_row_spans: list[tuple[int, int]] = field(default_factory=list)
    code_fences: int = 0
    inline_code: Counter = field(default_factory=Counter)
    bold_markers: int = 0
    links: Counter = field(default_factory=Counter)  # URLs
    math: list[tuple[int, int, str]] = field(default_factory=list)
    math_cmds: Counter = field(default_factory=Counter)
    envs: Counter = field(default_factory=Counter)
    dollar_count: int = 0


def structure(text: str) -> Structure:
    st = Structure()
    for m in HEADING.finditer(text):
        st.headings[len(m.group(1))] += 1
    st.list_items = len(LIST_ITEM.findall(text))
    for m in TABLE_ROW.finditer(text):
        row = m.group(0)
        if TABLE_SEP.match(row):
            continue
        cells = [c for c in regex.split(r"(?<!\\)\|", row.strip())[1:-1]]
        st.table_rows.append(len(cells))
        st.table_row_spans.append((m.start(), m.end()))
    st.code_fences = len(CODE_FENCE.findall(text))
    st.inline_code = Counter(INLINE_CODE.findall(text))
    no_math = text
    st.math = math_segments(text)
    for s, e, _ in reversed(st.math):
        no_math = no_math[:s] + " " * (e - s) + no_math[e:]
    st.bold_markers = len(BOLD.findall(no_math))
    st.links = Counter(m.group(2) for m in LINK.finditer(text))
    for _, _, body in st.math:
        st.math_cmds.update(c for c in LATEX_CMD.findall(LATEX_TEXT_ARG.sub("", body)))
    for kind, name in ENV.findall(text):
        st.envs[(kind, name)] += 1
    st.dollar_count = len(regex.findall(r"(?<!\\)\$", text))
    return st


def speakable(text: str) -> tuple[str, list[int]]:
    """Strip markup for TTS. Returns (plain_text, idx) with idx mapping plain chars to `text`.

    Math, code and URLs are dropped (not spoken); table pipes and markdown markers become spaces.
    """
    drop = [False] * len(text)
    for s, e, _ in math_segments(text):
        drop[s:e] = [True] * (e - s)
    for rx in (regex.compile(r"```.*?```", regex.S), INLINE_CODE, URL):
        for m in rx.finditer(text):
            drop[m.start():m.end()] = [True] * (m.end() - m.start())
    for m in LINK.finditer(text):  # keep link text, drop the URL part
        url_start = m.start(2) - 2
        drop[url_start:m.end()] = [True] * (m.end() - url_start)
    for line in regex.finditer(r"^.*$", text, regex.M):
        if TABLE_SEP.match(line.group(0)) and "-" in line.group(0):
            drop[line.start():line.end()] = [True] * (line.end() - line.start())
    out, idx = [], []
    for i, ch in enumerate(text):
        if drop[i]:
            continue
        c = " " if ch in "|#*_>`~" else ch
        out.append(c)
        idx.append(i)
    return "".join(out), idx
