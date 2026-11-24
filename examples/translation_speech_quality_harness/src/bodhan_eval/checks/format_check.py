"""Markdown, table and LaTeX integrity: structure in the translation must match the source."""
from __future__ import annotations

from collections import Counter

import regex

from ..text.markup import BOLD, LINK, has_markup, math_fingerprint, structure
from .base import Check, Context, Signal, register


@register
class FormatCheck(Check):
    name = "format"
    tier = 0

    def applies(self, ctx: Context) -> bool:
        return bool(ctx.translation) and (has_markup(ctx.item.source) or has_markup(ctx.translation))

    async def run(self, ctx: Context) -> list[Signal]:
        src, tr = ctx.item.source, ctx.translation
        a, b = structure(src), structure(tr)
        out: list[Signal] = []

        def sig(sev, conf, msg, span=None, **ev):
            out.append(Signal(self.name, "format", sev, conf, msg, span, None, ev))

        # tables: every row must keep the source's column count
        if a.table_rows:
            expected = Counter(a.table_rows).most_common(1)[0][0]
            bad = [(n, sp) for n, sp in zip(b.table_rows, b.table_row_spans, strict=False) if n != expected]
            for n, sp in bad[:3]:
                sig("major", 0.92, f"Table row has {n} cells; the source table has {expected}.", sp, expected_cells=expected, cells=n)
            if len(b.table_rows) < len(a.table_rows) and not bad:
                sig("major", 0.85, f"Table has {len(b.table_rows)} rows; the source has {len(a.table_rows)}.", None,
                    source_rows=len(a.table_rows), rows=len(b.table_rows))
        # LaTeX: math is copied verbatim, so fingerprints must match in order
        if a.dollar_count % 2 == 0 and b.dollar_count % 2 == 1:
            pos = [m.start() for m in regex.finditer(r"(?<!\\)\$", tr)]
            sig("major", 0.93, "Unbalanced $ math delimiter.", (pos[-1], pos[-1] + 1) if pos else None)
        elif a.math or b.math:
            # Compared as multisets: verb-final languages legitimately reorder clauses, and the math moves with them.
            fa = Counter(math_fingerprint(x[2]) for x in a.math)
            fb = [(s, e, math_fingerprint(body)) for s, e, body in b.math]
            missing = fa - Counter(fp for _, _, fp in fb)
            extra = Counter(fp for _, _, fp in fb) - fa
            if len(fb) != sum(fa.values()) and not extra:
                sig("major", 0.85, f"The translation has {len(fb)} math expressions; the source has {sum(fa.values())}.", None,
                    source=sum(fa.values()), translation=len(fb))
            for s, e, fp in fb:
                if extra[fp] > 0:
                    extra[fp] -= 1
                    sig("major", 0.9, "A LaTeX expression differs from the source.", (s, e), translation_math=fp[:120],
                        source_math_missing=sorted(missing)[:2])
        if a.envs != b.envs:
            sig("major", 0.88, "LaTeX \\begin/\\end environments differ from the source.", None,
                missing=sorted(f"{k}{{{n}}}" for (k, n) in (a.envs - b.envs)), extra=sorted(f"{k}{{{n}}}" for (k, n) in (b.envs - a.envs)))
        # Markdown
        if a.headings != b.headings:
            sig("major", 0.8, "Heading structure differs from the source.", _first_line_span(tr, r"^#{1,6}\s"),
                source=dict(a.headings), translation=dict(b.headings))
        if a.code_fences != b.code_fences:
            sig("major", 0.88, "Code fence count differs from the source.", None, source=a.code_fences, translation=b.code_fences)
        if a.bold_markers != b.bold_markers:
            span = None
            ms = list(BOLD.finditer(tr))
            if ms and b.bold_markers % 2 == 1:
                span = (ms[-1].start(), ms[-1].end())
            sev = "major" if b.bold_markers % 2 else "minor"
            sig(sev, 0.85 if b.bold_markers % 2 else 0.6, "Bold/emphasis markers are unbalanced or missing.", span,
                source=a.bold_markers, translation=b.bold_markers)
        if a.links != b.links:
            missing = sorted((a.links - b.links).elements())
            span = None
            changed = sorted((b.links - a.links).elements())
            for m in LINK.finditer(tr):
                if m.group(2) in changed:
                    span = (m.start(), m.end())
                    break
            sig("major", 0.9, "Link URLs differ from the source.", span, missing=missing[:5], changed=changed[:5])
        if a.inline_code != b.inline_code:
            sig("minor", 0.7, "Inline code differs from the source.", None, missing=sorted((a.inline_code - b.inline_code).elements())[:5])
        if a.list_items and abs(a.list_items - b.list_items) >= 1:
            sig("minor", 0.6, f"The translation has {b.list_items} list items; the source has {a.list_items}.", None)
        return out


def _first_line_span(text: str, pat: str):
    m = regex.search(pat + r".*$", text, regex.M)
    return (m.start(), m.end()) if m else None
