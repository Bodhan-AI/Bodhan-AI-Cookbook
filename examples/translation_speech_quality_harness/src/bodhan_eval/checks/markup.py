"""Markdown / LaTeX / verbatim-region parsing used by the format, script and term checks.

Deliberately a light, line-based parser: we compare the *structure* of source and
translation, so we need consistent block boundaries, not a full CommonMark renderer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Verbatim regions: content that must survive translation unchanged (or be excluded
# from script statistics): code, math, URLs, e-mails, HTML tags.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Region:
    kind: str  # code_block | inline_code | math_display | math_inline | url | email | html
    start: int
    end: int
    text: str


_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("code_block", re.compile(r"```.*?(?:```|\Z)", re.S)),
    ("math_display", re.compile(r"\$\$.+?\$\$|\\\[.+?\\\]", re.S)),
    ("inline_code", re.compile(r"`[^`\n]+`")),
    ("math_inline", re.compile(r"\\\(.+?\\\)")),
    # Pandoc rule: opening $ followed by non-space, closing $ preceded by non-space and not
    # followed by a digit. Keeps "costs $5 and $10" from being read as math.
    ("math_inline", re.compile(r"(?<![\\$])\$(?=\S)(?:\\\$|[^$\n])+?(?<=\S)\$(?![\d$])")),
    ("url", re.compile(r"https?://[^\s<>()\[\]]+[^\s<>()\[\].,;:!?'\"]")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("html", re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>")),
]


def protected_regions(text: str) -> list[Region]:
    taken: list[tuple[int, int]] = []
    out: list[Region] = []
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            out.append(Region(kind, s, e, m.group(0)))
    return sorted(out, key=lambda r: r.start)


def mask_regions(text: str, regions: list[Region]) -> str:
    """Replace regions with spaces (length-preserving)."""
    chars = list(text)
    for r in regions:
        for i in range(r.start, r.end):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


# ---------------------------------------------------------------------------
# Markdown block structure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    kind: str  # h1..h6 | ul | ol | table_row | table_sep | quote | fence | math | hr | para
    start: int
    end: int
    cells: int | None = None

    @property
    def structural(self) -> bool:
        return self.kind != "para"


_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def table_cells(line: str) -> int:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return len(re.split(r"(?<!\\)\|", s))


def md_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    pos = 0
    in_fence = False
    in_math = False
    para_start: int | None = None
    para_end = 0
    lines = text.split("\n")

    def flush_para():
        nonlocal para_start
        if para_start is not None:
            blocks.append(Block("para", para_start, para_end))
            para_start = None

    for line in lines:
        start, end = pos, pos + len(line)
        pos = end + 1
        stripped = line.strip()
        if in_fence:
            if stripped.startswith("```"):
                in_fence = False
            continue
        if in_math:
            if stripped.endswith("$$") or stripped.endswith("\\]"):
                in_math = False
            continue
        if not stripped:
            flush_para()
            continue
        kind, cells = None, None
        if stripped.startswith("```"):
            kind, in_fence = "fence", True
        elif stripped.startswith("$$") or stripped.startswith("\\["):
            kind = "math"
            body = stripped[2:]
            if not (body.endswith("$$") or body.endswith("\\]")):
                in_math = True
        elif m := re.match(r"^(#{1,6})\s+\S", stripped):
            kind = f"h{len(m.group(1))}"
        elif re.match(r"^([-*_])(\s*\1){2,}\s*$", stripped):
            kind = "hr"
        elif re.match(r"^[-*+]\s+", stripped):
            kind = "ul"
        elif re.match(r"^\d+[.)]\s+", stripped):
            kind = "ol"
        elif stripped.startswith(">"):
            kind = "quote"
        elif "|" in stripped and (stripped.startswith("|") or _TABLE_SEP.match(stripped)):
            kind = "table_sep" if _TABLE_SEP.match(stripped) else "table_row"
            cells = table_cells(stripped)
        if kind:
            flush_para()
            blocks.append(Block(kind, start, end, cells))
        else:
            if para_start is None:
                para_start = start
            para_end = end
    flush_para()
    return blocks


# ---------------------------------------------------------------------------
# LaTeX
# ---------------------------------------------------------------------------

_TEXT_CMDS = re.compile(r"\\(?:text|mathrm|textrm|textbf|textit|mbox|operatorname)\s*\{[^{}]*\}")
_CMD = re.compile(r"\\([a-zA-Z]+)")


def latex_commands(math: str) -> list[str]:
    """Command names in a math region, ignoring the contents of \\text{...} (translatable)."""
    return sorted(_CMD.findall(_TEXT_CMDS.sub(" ", math)))


def brace_balance_ok(math: str) -> bool:
    depth = 0
    i = 0
    while i < len(math):
        ch = math[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def env_balance(text: str) -> dict[str, int]:
    """begin minus end count per LaTeX environment (non-zero = unbalanced)."""
    bal: dict[str, int] = {}
    for m in re.finditer(r"\\(begin|end)\{([^}]+)\}", text):
        bal[m.group(2)] = bal.get(m.group(2), 0) + (1 if m.group(1) == "begin" else -1)
    return {k: v for k, v in bal.items() if v}


def stray_dollars(text: str, regions: list[Region]) -> list[int]:
    """Positions of '$' not inside any recognised math region and not currency-like."""
    inside = [(r.start, r.end) for r in regions if r.kind.startswith("math")]
    out = []
    for m in re.finditer(r"(?<!\\)\$", text):
        p = m.start()
        if any(s <= p < e for s, e in inside):
            continue
        nxt = text[p + 1 : p + 2]
        if nxt.isdigit():  # "$5": currency
            continue
        out.append(p)
    return out
