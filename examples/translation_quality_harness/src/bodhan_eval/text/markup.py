"""Markdown and LaTeX structure extraction and source/translation parity.

Each line gets a structural signature (heading level, list item, table row with its column
count, math block, ...). The source and translation signature sequences are aligned with
difflib, so a broken table row or a dropped heading is reported on the exact line.
"""

from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field

import regex

DISPLAY_MATH = regex.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", regex.DOTALL)
INLINE_MATH = regex.compile(r"\\\((.+?)\\\)|(?<![\\$])\$(?![\s$\d])([^$\n]+?)(?<![\s\\])\$(?!\$)")
INLINE_CODE = regex.compile(r"`([^`\n]+)`")
LINK = regex.compile(r"!?\[[^\]\n]*\]\(([^)\s]+)[^)]*\)")
HTML_TAG = regex.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
BEGIN_END = regex.compile(r"\\(begin|end)\{([^}]+)\}")
TEXT_CMD = regex.compile(r"\\(?:text|mathrm|textbf|textit|operatorname|mbox)\{[^{}]*\}")
TABLE_SEP = regex.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
LIST_ITEM = regex.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEADING = regex.compile(r"^\s{0,3}(#{1,6})\s+")
HR = regex.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


@dataclass(frozen=True)
class Line:
    text: str
    start: int
    end: int


@dataclass
class MarkupIssue:
    message: str
    category: str = "format"
    span: tuple[int, int] | None = None
    source_span: tuple[int, int] | None = None
    data: dict = field(default_factory=dict)


def lines(text: str) -> list[Line]:
    out, pos = [], 0
    for raw in text.split("\n"):
        if raw.strip():
            out.append(Line(raw, pos, pos + len(raw)))
        pos += len(raw) + 1
    return out


def table_cells(line: str) -> int:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return len(regex.split(r"(?<!\\)\|", body))


def is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") or stripped.count("|") >= 2


def signature(line: str) -> tuple:
    stripped = line.strip()
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return ("fence",)
    if stripped.startswith("$$") or stripped.startswith("\\["):
        return ("math_block",)
    if HR.match(line):
        return ("hr",)
    m = HEADING.match(line)
    if m:
        return ("heading", len(m.group(1)))
    if TABLE_SEP.match(line) and "|" in line:
        return ("table_sep", table_cells(line))
    if is_table_row(line):
        return ("table_row", table_cells(line))
    if stripped.startswith(">"):
        return ("quote",)
    if LIST_ITEM.match(line):
        return ("list_item",)
    return ("text",)


def has_markup(text: str) -> bool:
    if "\n" in text.strip() and any(signature(ln.text) != ("text",) for ln in lines(text)):
        return True
    return bool(math_segments(text) or INLINE_CODE.search(text) or LINK.search(text) or "**" in text or BEGIN_END.search(text))


@dataclass(frozen=True)
class MathSegment:
    content: str
    start: int
    end: int
    display: bool


def math_segments(text: str) -> list[MathSegment]:
    segs: list[MathSegment] = []
    taken: list[tuple[int, int]] = []
    for m in DISPLAY_MATH.finditer(text):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        segs.append(MathSegment(body, m.start(), m.end(), True))
        taken.append((m.start(), m.end()))
    for m in INLINE_MATH.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        body = m.group(1) if m.group(1) is not None else m.group(2)
        segs.append(MathSegment(body, m.start(), m.end(), False))
    return sorted(segs, key=lambda s: s.start)


def canonical_math(content: str) -> str:
    return regex.sub(r"\s+", "", TEXT_CMD.sub(r"\\text{}", content))


def _digits_only_difference(a: str, b: str) -> bool:
    return regex.sub(r"\d+", "#", a) == regex.sub(r"\d+", "#", b) and a != b


def _inline_features(text: str) -> Counter:
    feats: Counter = Counter()
    feats["bold"] = text.count("**") + text.count("__")
    feats["code"] = len(INLINE_CODE.findall(text))
    feats["links"] = len(LINK.findall(text))
    feats["math"] = len(math_segments(text))
    return feats


def _unbalanced(text: str, open_ch: str, close_ch: str) -> int | None:
    depth = 0
    for i, ch in enumerate(text):
        if ch == open_ch and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif ch == close_ch and (i == 0 or text[i - 1] != "\\"):
            depth -= 1
            if depth < 0:
                return i
    return None if depth == 0 else len(text) - 1


def unescaped_dollars(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch == "$" and (i == 0 or text[i - 1] != "\\")]


def compare(source: str, translation: str) -> list[MarkupIssue]:
    issues: list[MarkupIssue] = []
    issues += _compare_lines(source, translation)
    issues += _compare_math(source, translation)
    issues += _compare_inline_tokens(source, translation)
    issues += _check_balance(source, translation)
    return _dedupe(issues)


def _compare_lines(source: str, translation: str) -> list[MarkupIssue]:
    src_lines, tgt_lines = lines(source), lines(translation)
    if len(src_lines) <= 1 and len(tgt_lines) <= 1 and all(signature(x.text) == ("text",) for x in src_lines + tgt_lines):
        return []
    src_sig = [signature(x.text) for x in src_lines]
    tgt_sig = [signature(x.text) for x in tgt_lines]
    issues: list[MarkupIssue] = []
    matcher = difflib.SequenceMatcher(a=src_sig, b=tgt_sig, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                issues += _compare_inline(src_lines[i1 + k], tgt_lines[j1 + k])
            continue
        src_block = src_lines[i1:i2]
        tgt_block = tgt_lines[j1:j2]
        only_text = all(s == ("text",) for s in src_sig[i1:i2] + tgt_sig[j1:j2])
        if only_text:
            continue  # paragraph re-flow is not a format error
        src_span = (src_block[0].start, src_block[-1].end) if src_block else None
        if tgt_block:
            tgt_span = (tgt_block[0].start, tgt_block[-1].end)
        else:
            anchor = tgt_lines[j1 - 1].end if j1 > 0 and tgt_lines else 0
            tgt_span = (anchor, anchor)
        expected = ", ".join(_describe(s) for s in src_sig[i1:i2]) or "nothing"
        found = ", ".join(_describe(s) for s in tgt_sig[j1:j2]) or "nothing"
        issues.append(
            MarkupIssue(
                f"structure mismatch: source has {expected}; translation has {found}",
                span=tgt_span,
                source_span=src_span,
                data={"expected": [list(s) for s in src_sig[i1:i2]], "found": [list(s) for s in tgt_sig[j1:j2]]},
            )
        )
    return issues


def _describe(sig: tuple) -> str:
    kind = sig[0]
    if kind == "heading":
        return f"heading level {sig[1]}"
    if kind in {"table_row", "table_sep"}:
        return f"{kind.replace('_', ' ')} with {sig[1]} cells"
    return kind.replace("_", " ")


def _compare_inline(src: Line, tgt: Line) -> list[MarkupIssue]:
    fs, ft = _inline_features(src.text), _inline_features(tgt.text)
    diffs = {k: (fs[k], ft[k]) for k in fs if fs[k] != ft[k]}
    if not diffs:
        return []
    desc = "; ".join(f"{k}: source {a}, translation {b}" for k, (a, b) in diffs.items())
    return [MarkupIssue(f"inline markup mismatch on line ({desc})", span=(tgt.start, tgt.end), source_span=(src.start, src.end), data={"diff": diffs})]


def _compare_math(source: str, translation: str) -> list[MarkupIssue]:
    src_math, tgt_math = math_segments(source), math_segments(translation)
    issues: list[MarkupIssue] = []
    if not src_math and not tgt_math:
        return issues
    src_keys = [canonical_math(m.content) for m in src_math]
    tgt_keys = [canonical_math(m.content) for m in tgt_math]
    matcher = difflib.SequenceMatcher(a=src_keys, b=tgt_keys, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        pairs = list(zip(range(i1, i2), range(j1, j2), strict=False))
        for si, ti in pairs:
            s, t = src_math[si], tgt_math[ti]
            category = "number" if _digits_only_difference(src_keys[si], tgt_keys[ti]) else "format"
            issues.append(
                MarkupIssue(
                    f"LaTeX changed: source `{s.content.strip()}` vs translation `{t.content.strip()}`",
                    category=category,
                    span=(t.start, t.end),
                    source_span=(s.start, s.end),
                )
            )
        for si in range(i1 + len(pairs), i2):
            s = src_math[si]
            anchor = tgt_math[j1 - 1].end if j1 > 0 else 0
            issues.append(MarkupIssue(f"LaTeX segment missing from translation: `{s.content.strip()}`", span=(anchor, anchor), source_span=(s.start, s.end)))
        for ti in range(j1 + len(pairs), j2):
            t = tgt_math[ti]
            issues.append(MarkupIssue(f"LaTeX segment not in source: `{t.content.strip()}`", span=(t.start, t.end)))
    return issues


def _compare_inline_tokens(source: str, translation: str) -> list[MarkupIssue]:
    issues: list[MarkupIssue] = []
    src_code = Counter(INLINE_CODE.findall(source))
    for m in INLINE_CODE.finditer(translation):
        if src_code and m.group(1) not in src_code:
            issues.append(MarkupIssue(f"inline code changed: `{m.group(1)}` is not in the source", span=(m.start(), m.end())))
    src_urls = Counter(LINK.findall(source))
    for m in LINK.finditer(translation):
        if src_urls and m.group(1) not in src_urls:
            issues.append(MarkupIssue(f"link target changed: {m.group(1)}", span=(m.start(), m.end())))
    src_env = Counter(BEGIN_END.findall(source))
    tgt_env = Counter(BEGIN_END.findall(translation))
    for key, count in src_env.items():
        if tgt_env[key] < count:
            sm = next(m for m in BEGIN_END.finditer(source) if (m.group(1), m.group(2)) == key)
            issues.append(MarkupIssue(f"\\{key[0]}{{{key[1]}}} missing from translation", source_span=(sm.start(), sm.end()), span=None))
    src_tags = Counter(t.lower() for t in HTML_TAG.findall(source))
    tgt_tags = Counter(t.lower() for t in HTML_TAG.findall(translation))
    if src_tags != tgt_tags and (src_tags or tgt_tags):
        diff = dict((src_tags - tgt_tags) + (tgt_tags - src_tags))
        issues.append(MarkupIssue(f"HTML tags differ: {diff}", data={"diff": diff}))
    return issues


def _check_balance(source: str, translation: str) -> list[MarkupIssue]:
    issues: list[MarkupIssue] = []
    src_d, tgt_d = unescaped_dollars(source), unescaped_dollars(translation)
    if len(src_d) % 2 == 0 and len(tgt_d) % 2 == 1:
        pos = _lonely_dollar(translation, tgt_d)
        issues.append(MarkupIssue("unbalanced $ delimiter in translation", span=(pos, pos + 1)))
    for open_ch, close_ch in (("{", "}"), ("(", ")"), ("[", "]")):
        if _unbalanced(source, open_ch, close_ch) is None:
            pos = _unbalanced(translation, open_ch, close_ch)
            if pos is not None:
                issues.append(MarkupIssue(f"unbalanced {open_ch}{close_ch} in translation", span=(pos, pos + 1)))
    if source.count("**") % 2 == 0 and translation.count("**") % 2 == 1:
        pos = translation.rfind("**")
        issues.append(MarkupIssue("unpaired ** bold marker in translation", span=(pos, pos + 2)))
    if source.count("```") % 2 == 0 and translation.count("```") % 2 == 1:
        pos = translation.rfind("```")
        issues.append(MarkupIssue("unclosed code fence in translation", span=(pos, pos + 3)))
    return issues


def _lonely_dollar(text: str, positions: list[int]) -> int:
    paired = set()
    for seg in math_segments(text):
        paired.update(range(seg.start, seg.end))
    for p in positions:
        if p not in paired:
            return p
    return positions[-1]


def _dedupe(issues: list[MarkupIssue]) -> list[MarkupIssue]:
    seen, out = set(), []
    for issue in issues:
        key = (issue.message, issue.span, issue.source_span)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out
