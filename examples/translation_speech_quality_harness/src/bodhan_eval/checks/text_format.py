"""Structural format check: the translation must keep the source's markup skeleton.

The source is the structural ground truth, so no reference translation is needed.
Prose is free to change; headings, list markers, table shape, code fences, math
delimiters, LaTeX commands, links, placeholders and HTML tags must survive.
"""

from collections import Counter
import re

from ..config import CHECK_DEFAULTS
from ..models import Flag, Item
from .registry import LangStats, register_check

_Token = tuple[str, int, int]

_HEADING = re.compile(r"(?m)^[ \t]*(#{1,6})[ \t]+")
_LIST = re.compile(r"(?m)^[ \t]*(?:([-*+])|(\d+[.)]))[ \t]+")
_FENCE = re.compile(r"(?m)^[ \t]*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_TABLE_ROW = re.compile(r"(?m)^[ \t]*\|.*\|[ \t]*$")
_TABLE_SEP = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$")
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"(?<![$\\])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?!\$)")
_PAREN_MATH = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_BRACKET_MATH = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_LATEX_CMD = re.compile(r"\\[A-Za-z]+")
_MATHY = re.compile(r"[\\^_{}=]|^\s*[A-Za-z]\s*$")
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)[^)]*\)")
_URL = re.compile(r"https?://[^\s)>\]]+")
_PLACEHOLDER = re.compile(r"\{\{?[A-Za-z_][\w.]*\}\}?|%\d*\$?[sdf]\b")
_HTML = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)(?:\s[^<>]*)?>")
_BOLD = re.compile(r"\*\*|__")
# Extra markup the source never had is only a defect for tokens that must survive verbatim;
# added emphasis, headings, lists or tables are style, not breakage.
_STRICT_EXTRA = ("fence", "math", "cmd:", "ph:", "url:", "link:", "html:")


def _tokens(text: str) -> list[_Token]:
    found: list[_Token] = []

    def add(pattern: re.Pattern[str], make) -> None:
        for match in pattern.finditer(text):
            found.append((make(match), match.start(), match.end()))

    add(_HEADING, lambda m: f"h{len(m.group(1))}")
    add(_LIST, lambda m: "ul" if m.group(1) else "ol")
    add(_FENCE, lambda m: "fence")
    add(_INLINE_CODE, lambda m: "code")
    for match in _TABLE_ROW.finditer(text):
        row = match.group(0)
        if _TABLE_SEP.match(row):
            token = "tblsep"
        else:
            token = f"tbl{len(row.strip().strip('|').split('|'))}"
        found.append((token, match.start(), match.end()))

    blanked = text
    for pattern, name in ((_DISPLAY_MATH, "math$$"), (_PAREN_MATH, "math\\("), (_BRACKET_MATH, "math\\[")):
        for match in pattern.finditer(blanked):
            found.append((name, match.start(), match.end()))
        blanked = pattern.sub(lambda m: " " * len(m.group(0)), blanked)
    for match in _INLINE_MATH.finditer(blanked):
        if _MATHY.search(match.group(1)):
            found.append(("math$", match.start(), match.end()))

    add(_LATEX_CMD, lambda m: f"cmd:{m.group(0)}")
    add(_LINK, lambda m: f"link:{m.group(1)}")
    add(_URL, lambda m: f"url:{m.group(0)}")
    add(_PLACEHOLDER, lambda m: f"ph:{m.group(0)}")
    add(_HTML, lambda m: f"html:{m.group(0)[:2] == '</' and '/' or ''}{m.group(1).lower()}")
    add(_BOLD, lambda m: "bold")
    return sorted(found, key=lambda token: (token[1], token[0]))


def _line_span(text: str, offset: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return (start, len(text) if end < 0 else end)


def _line_index(text: str, offset: int) -> int:
    return text.count("\n", 0, offset)


def _nth_line_span(text: str, index: int) -> tuple[int, int] | None:
    lines = text.split("\n")
    if index >= len(lines):
        return None
    start = sum(len(line) + 1 for line in lines[:index])
    return (start, start + len(lines[index]))


@register_check(name="text_format", categories=["format"], modality="text", cost_tier=0)
def check_format(item: Item, stats: LangStats | None = None) -> list[Flag]:
    del stats
    if not item.translation or not item.source:
        return []
    source_tokens = _tokens(item.source)
    target_tokens = _tokens(item.translation)
    if not source_tokens and not target_tokens:
        return []
    source_counts = Counter(token for token, _, _ in source_tokens)
    target_counts = Counter(token for token, _, _ in target_tokens)
    missing = source_counts - target_counts
    extra = Counter({
        token: count
        for token, count in (target_counts - source_counts).items()
        if token.startswith(_STRICT_EXTRA)
    })
    if not missing and not extra:
        return []

    translation = item.translation
    source_span: tuple[int, int] | None = None
    span: tuple[int, int]
    if missing:
        first = next(token for token in source_tokens if token[0] in missing)
        source_span = (first[1], first[2])
        source_lines = item.source.count("\n")
        target_lines = translation.count("\n")
        mapped = (
            _nth_line_span(translation, _line_index(item.source, first[1]))
            if source_lines == target_lines
            else None
        )
        span = mapped if mapped is not None else (0, len(translation))
    else:
        first = next(token for token in target_tokens if token[0] in extra)
        span = (first[1], first[2])
    return [Flag(
        category="format",
        severity="major",
        modality="text",
        span=span,
        source_span=source_span,
        evidence={
            "missing": sorted(missing),
            "extra": sorted(extra),
        },
        detectors=["text_format"],
        confidence=CHECK_DEFAULTS["confidence"]["text_format"],
    )]
