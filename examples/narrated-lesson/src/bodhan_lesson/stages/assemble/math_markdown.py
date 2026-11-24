"""Make sure every element's math is valid Markdown+LaTeX that a renderer (KaTeX) can parse.

OCR returns equation blocks in several shapes: bare LaTeX, one `$$...$$` span, inline
`$...$` spans mixed with words and equation labels, or malformed text. Nothing here
depends on the document: shapes are recognised from the delimiters alone.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class MathSpan:
    start: int
    end: int  # exclusive, includes the closing delimiter
    delimiter: str  # "$" or "$$"
    body: str


def has_delimiters(text: str) -> bool:
    """True if the text contains any unescaped `$`."""
    return "$" in text.replace("\\$", "")


def _find_close(text: str, start: int, delimiter: str) -> int | None:
    index = start
    while index < len(text):
        if text.startswith("\\$", index):
            index += 2
        elif text.startswith(delimiter, index):
            return index
        else:
            index += 1
    return None


def scan_math(text: str) -> tuple[list[MathSpan], list[str]]:
    """Find math spans in order; also report delimiters that never close."""
    spans: list[MathSpan] = []
    problems: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("\\$", index):
            index += 2
            continue
        if text[index] != "$":
            index += 1
            continue
        delimiter = "$$" if text.startswith("$$", index) else "$"
        close = _find_close(text, index + len(delimiter), delimiter)
        if close is None:
            problems.append("unterminated_display_math" if delimiter == "$$" else "unterminated_inline_math")
            index += len(delimiter)
            continue
        spans.append(MathSpan(index, close + len(delimiter), delimiter, text[index + len(delimiter):close]))
        index = close + len(delimiter)
    return spans, problems


# ---- per-span checks -----------------------------------------------------------
_ESCAPED_BRACE = re.compile(r"\\[{}]")
_ENV = re.compile(r"\\(begin|end)\{([^}]*)\}")


def span_problems(span: MathSpan) -> list[str]:
    body = span.body
    problems = []
    if not body.strip():
        problems.append("empty_math")
    if has_delimiters(body):
        problems.append("nested_dollar")
    plain = _ESCAPED_BRACE.sub("", body)
    depth = 0
    for char in plain:
        depth += (char == "{") - (char == "}")
        if depth < 0:
            break
    if depth != 0:
        problems.append("unbalanced_braces")
    if len(re.findall(r"\\left\b", body)) != len(re.findall(r"\\right\b", body)):
        problems.append("unbalanced_left_right")
    opened, closed = Counter(), Counter()
    for kind, name in _ENV.findall(body):
        (opened if kind == "begin" else closed)[name] += 1
    if opened != closed:
        problems.append("unbalanced_environment")
    if span.delimiter == "$" and re.search(r"\n\s*\n", body):
        problems.append("blank_line_in_inline_math")
    return problems


def math_problems(text: str) -> list[str]:
    """Every problem in `text`, without repetition."""
    spans, problems = scan_math(text)
    for span in spans:
        problems += span_problems(span)
    return list(dict.fromkeys(problems))


# ---- shaping and repair --------------------------------------------------------
def is_single_display_span(text: str) -> bool:
    spans, problems = scan_math(text.strip())
    stripped = text.strip()
    return not problems and len(spans) == 1 and spans[0].delimiter == "$$" \
        and spans[0].start == 0 and spans[0].end == len(stripped)


def shape_equation(text: str) -> str:
    """Markdown for an OCR `Equation` block.

    - no delimiters: bare LaTeX, so wrap it as display math;
    - exactly one `$$...$$` span: already display math, keep it;
    - otherwise (inline spans mixed with words or an equation label): keep it as a line of
      text. Wrapping it in `$$` would nest `$` inside display math, which KaTeX rejects.
    """
    text = text.strip()
    if not has_delimiters(text):
        return f"$$\n{text}\n$$"
    return text


def repair_nested_dollars(text: str) -> str:
    """Drop `$` that sit inside a `$$...$$` span (the only repair that cannot change meaning)."""
    spans, _ = scan_math(text)
    out, cursor = [], 0
    for span in spans:
        out.append(text[cursor:span.start])
        if span.delimiter == "$$" and has_delimiters(span.body):
            body = re.sub(r"(?<!\\)\$", "", span.body)
            out.append(f"$${body}$$")
        else:
            out.append(text[span.start:span.end])
        cursor = span.end
    out.append(text[cursor:])
    return "".join(out)
