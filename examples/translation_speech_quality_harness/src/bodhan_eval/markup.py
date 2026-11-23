"""Markdown and LaTeX structure extraction (pure functions, no model calls)."""

from __future__ import annotations

import re
from dataclasses import dataclass

MATH_RE = re.compile(
    r"(?P<dd>(?<!\\)\$\$(?P<dd_body>.+?)(?<!\\)\$\$)"
    r"|(?P<br>\\\[(?P<br_body>.+?)\\\])"
    r"|(?P<pa>\\\((?P<pa_body>.+?)\\\))"
    r"|(?P<env>\\begin\{(?P<env_name>[a-zA-Z*]+)\}(?P<env_body>.+?)\\end\{(?P=env_name)\})"
    r"|(?P<d>(?<![\\$])\$(?!\$)(?P<d_body>[^$\n]+?)(?<!\\)\$)",
    re.DOTALL,
)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
URL_RE = re.compile(r"https?://[^\s)\]>]+")
LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")
TEXT_CMD_RE = re.compile(r"\\(?:text|textrm|textbf|mbox)\{[^{}]*\}")


@dataclass(frozen=True)
class MathSeg:
    start: int
    end: int
    body: str
    kind: str

    @property
    def key(self) -> str:
        return math_key(self.body)


def math_key(body: str) -> str:
    """Comparison key for a formula: \\text{...} contents removed (they may be translated), whitespace removed,
    and braces around a one-token super/subscript dropped (``x^{2}`` renders exactly like ``x^2``)."""
    key = re.sub(r"\s+", "", TEXT_CMD_RE.sub(r"\\text{}", body))
    return re.sub(r"([\^_])\{([A-Za-z0-9]|\\[A-Za-z]+)\}", r"\1\2", key)


def math_segments(text: str) -> list[MathSeg]:
    out = []
    for m in MATH_RE.finditer(text):
        for kind in ("dd", "br", "pa", "env", "d"):
            if m.group(kind):
                body = m.group(f"{kind}_body") if kind != "env" else m.group("env_name") + ":" + m.group("env_body")
                out.append(MathSeg(m.start(), m.end(), body, kind))
                break
    return out


def protected_spans(text: str) -> list[tuple[int, int]]:
    """Regions whose content must survive translation untouched: math, inline code, code blocks, URLs."""
    spans = [(s.start, s.end) for s in math_segments(text)]
    spans += [(m.start(), m.end()) for m in INLINE_CODE_RE.finditer(text)]
    spans += [(m.start(), m.end()) for m in URL_RE.finditer(text)]
    for m in re.finditer(r"```.*?```", text, re.DOTALL):
        spans.append((m.start(), m.end()))
    return sorted(spans)


def in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def unbalanced_dollars(text: str) -> list[int]:
    """Positions of '$' delimiters left unmatched after removing well-formed math."""
    masked = list(text)
    for s in math_segments(text):
        for k in range(s.start, s.end):
            masked[k] = " "
    t = "".join(masked)
    return [m.start() for m in re.finditer(r"(?<!\\)\$", t)]


def brace_problems(body: str) -> bool:
    depth = 0
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                return True
        i += 1
    return depth != 0


@dataclass(frozen=True)
class Line:
    start: int
    end: int
    kind: str  # heading1..6, ul, ol, table, table_sep, fence, quote, blank, text
    cols: int = 0


_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def table_cols(line: str) -> int:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return len(re.split(r"(?<!\\)\|", s))


def md_lines(text: str) -> list[Line]:
    lines = []
    pos = 0
    for raw in text.split("\n"):
        start, end = pos, pos + len(raw)
        pos = end + 1
        s = raw.strip()
        if not s:
            kind, cols = "blank", 0
        elif s.startswith("```"):
            kind, cols = "fence", 0
        elif m := re.match(r"^(#{1,6})\s", s):
            kind, cols = f"heading{len(m.group(1))}", 0
        elif _TABLE_SEP.match(s) and "-" in s and "|" in s:
            kind, cols = "table_sep", table_cols(s)
        elif s.startswith("|") or (s.count("|") >= 2 and not s.startswith("$")):
            kind, cols = "table", table_cols(s)
        elif re.match(r"^[-*+]\s", s):
            kind, cols = "ul", 0
        elif re.match(r"^\d+[.)]\s", s):
            kind, cols = "ol", 0
        elif s.startswith(">"):
            kind, cols = "quote", 0
        else:
            kind, cols = "text", 0
        lines.append(Line(start, end, kind, cols))
    return lines


def has_markup(text: str) -> bool:
    if math_segments(text) or "$" in text or INLINE_CODE_RE.search(text) or "**" in text or LINK_RE.search(text):
        return True
    return any(line.kind not in ("text", "blank") for line in md_lines(text))


def unmatched_marker_positions(text: str, marker: str) -> list[int]:
    """If ``marker`` (e.g. '**' or '`') occurs an odd number of times outside protected regions, return the last one."""
    prot = protected_spans(text) if marker != "`" else []
    pos = [m.start() for m in re.finditer(re.escape(marker), text) if not in_spans(m.start(), prot)]
    return [pos[-1]] if len(pos) % 2 == 1 else []
