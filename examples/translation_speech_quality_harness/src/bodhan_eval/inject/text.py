"""Programmatic edits of the Indian-language translation. No model writes any of this text.

Every method has the signature ``(item, rng, ctx) -> Edit | None`` and returns None when it does not apply to
the item (no number to change, no table to break...). Methods only delete, duplicate, move or swap text that is
already in the dataset: this item's translation, another item's translation in the same language, the English
source, or a name from another item's source. The one exception is a name's Indic rendering when the original
is in Indian script, which comes from Indic-Transliterate (a Bodhan model, ``--with-models`` only).

Severity follows how much meaning or rendering is lost (table in ``core.METHODS``).
"""

from __future__ import annotations

import random
import re
import unicodedata

from .. import markup
from ..budget import BudgetExceeded
from ..checks.roundtrip import extract_numbers, source_entities, transliterable
from ..clients import BodhanError, ServiceUnavailable
from ..config import defaults, lang_info
from ..models import Item
from ..normalize import Token, ascii_digits, clusters, normalize_text, segments, word_tokens
from ..spans import best_window_match
from .base import (
    Edit,
    InjectContext,
    Span,
    aligned,
    insert_after,
    prose_segments,
    remove,
    replace,
    sentence_body_end,
)

# ---------------------------------------------------------------------------
# omission
# ---------------------------------------------------------------------------


def drop_phrase(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Delete 2-3 words inside a sentence (never its first or last word, never a number)."""
    t = it.translation or ""
    cands: list[Span] = []
    for a, b in prose_segments(t, min_words=6):
        toks = word_tokens(t[a:b])
        for n in (2, 3):
            for i in range(1, len(toks) - n):
                run = toks[i : i + n]
                if not any(c.isdigit() for x in run for c in x.text):
                    cands.append((a + run[0].start, a + run[-1].end))
    if not cands:
        return None
    new, pos = remove(t, *rng.choice(cands))
    return Edit(span=(pos, pos), translation=new)


CLAUSE_SEP = re.compile(r"[,;]\s")  # a comma or semicolon and a space: "2,525" is not a clause boundary


def drop_clause(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Delete one comma/semicolon clause of a sentence; at least 3 words go and at least 3 stay."""
    t = it.translation or ""
    cands: list[Span] = []
    for a, b in prose_segments(t, min_words=6):
        seg = t[a:b]
        end = sentence_body_end(seg)
        bounds = [0, *(m.start() for m in CLAUSE_SEP.finditer(seg[:end])), end]
        total = len(word_tokens(seg))
        for k in range(len(bounds) - 1):
            lo, hi = bounds[k], bounds[k + 1]
            # the first clause goes with the separator after it; a later one with the separator before it
            cut = (lo, hi + 2) if k == 0 else (lo, hi)
            n = len(word_tokens(seg[lo:hi]))
            if len(bounds) > 2 and n >= 3 and total - n >= 3:
                cands.append((a + cut[0], a + cut[1]))
    if not cands:
        return None
    new, pos = remove(t, *rng.choice(cands))
    return Edit(span=(pos, pos), translation=new)


def drop_sentence(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Delete a whole sentence. Labelled on the English sentence when the sentences align, else at the gap."""
    t = it.translation or ""
    prose = prose_segments(t)
    if len(segments(t)) < 2 or not prose:
        return None
    a, b = rng.choice(prose)
    new, pos = remove(t, a, b)
    source_span = next((s for s, tgt in aligned(it) or [] if tgt == (a, b)), None)
    if source_span is not None:
        return Edit(span=source_span, span_on="source", translation=new)
    return Edit(span=(pos, pos), translation=new)


# ---------------------------------------------------------------------------
# addition
# ---------------------------------------------------------------------------


def repeat_sentence(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Repeat a sentence right after itself, minus its first word (a duplicated, slightly varied sentence)."""
    t = it.translation or ""
    cands = prose_segments(t, min_words=4)
    if not cands:
        return None
    a, b = rng.choice(cands)
    copy = t[a + word_tokens(t[a:b])[1].start : b]
    new, span = insert_after(t, b, copy)
    return Edit(span=span, translation=new)


def foreign_sentence(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Insert a sentence taken from another item's translation in the same language."""
    t = it.translation or ""
    texts = [o.translation for o in ctx.others(it) if o.translation]
    pool = sorted({o[a:b] for o in texts for a, b in prose_segments(o, 4)} - {t[a:b] for a, b in segments(t)})
    targets = prose_segments(t, min_words=1)
    if not pool or not targets:
        return None
    sentence = rng.choice(pool)
    _, b = rng.choice(targets)
    new, span = insert_after(t, b, sentence)
    return Edit(span=span, translation=new)


# ---------------------------------------------------------------------------
# mistranslation (the model-based ones are in english.py)
# ---------------------------------------------------------------------------


def content_words(text: str, lang: str) -> list[Token]:
    """Indian-script words of 3+ characters that are not function words, numbers or inside formulas."""
    function_words = set(lang_info(lang).function_words)
    protected = markup.protected_spans(text)
    return [
        tok
        for tok in word_tokens(text)
        if len(tok.text) >= 3
        and not tok.text.isascii()
        and not any(c.isdigit() for c in tok.text)
        and tok.text not in function_words
        and not markup.in_spans(tok.start, protected)
    ]


def word_swap(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Replace one content word with a word of similar length from another item: a wrong word in a sentence."""
    t = it.translation or ""
    targets = content_words(t, it.lang)
    pool = sorted({w.text for o in ctx.others(it) for w in content_words(o.translation or "", o.lang)})
    pool = [w for w in pool if w not in t]
    if not targets or not pool:
        return None
    tok = rng.choice(targets)
    near = [w for w in pool if abs(len(w) - len(tok.text)) <= 2] or pool
    new, span = replace(t, tok.start, tok.end, rng.choice(near))
    return Edit(span=span, translation=new)


# ---------------------------------------------------------------------------
# number
# ---------------------------------------------------------------------------


def numbers(text: str) -> list[Span]:
    """Numbers written in digits (any script), minus list markers ("1. " at a line start).
    Digits glued to Latin letters (H2O, CO2) are not numbers here, as in the number check."""
    out = []
    for _, a, b in extract_numbers(text):
        at_line_start = not text[text.rfind("\n", 0, a) + 1 : a].strip()
        if at_line_start and re.match(r"[.)]\s", text[b : b + 2]):
            continue
        out.append((a, b))
    return out


def _digits(text: str, a: int, b: int) -> list[int]:
    return [k for k in range(a, b) if text[k].isdigit()]


def _set_digit(ch: str, d: int) -> str:
    """The digit ``d`` in the same digit system as ``ch`` (२ -> ५ stays Devanagari)."""
    return chr(ord(ch) - unicodedata.digit(ch) + d)


def _change_digit(t: str, k: int, rng: random.Random, allow_zero: bool) -> str:
    old = unicodedata.digit(t[k])
    d = rng.choice([d for d in range(0 if allow_zero else 1, 10) if d != old])
    return t[:k] + _set_digit(t[k], d) + t[k + 1 :]


def first_digit(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Change the leading digit of a number (25 -> 45): a large change in value."""
    t = it.translation or ""
    nums = numbers(t)
    if not nums:
        return None
    a, b = rng.choice(nums)
    return Edit(span=(a, b), translation=_change_digit(t, _digits(t, a, b)[0], rng, allow_zero=False))


def last_digit(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Change the last digit of a number with 3+ digits (2,525 -> 2,527): a small change in value."""
    t = it.translation or ""
    nums = [(a, b) for a, b in numbers(t) if len(_digits(t, a, b)) >= 3]
    if not nums:
        return None
    a, b = rng.choice(nums)
    return Edit(span=(a, b), translation=_change_digit(t, _digits(t, a, b)[-1], rng, allow_zero=True))


def decimal_shift(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Drop a decimal point (3.5 -> 35): the value is off by a power of ten."""
    t = it.translation or ""
    nums = [(a, b) for a, b in numbers(t) if re.search(r"\d\.\d", ascii_digits(t[a:b]))]
    if not nums:
        return None
    a, b = rng.choice(nums)
    p = re.search(r"\d\.\d", ascii_digits(t[a:b]))
    assert p is not None
    dot = a + p.start() + 1
    new = t[:dot] + t[dot + 1 :]
    return Edit(span=(a, b - 1), translation=new)


# ---------------------------------------------------------------------------
# term (names and terms from the source)
# ---------------------------------------------------------------------------


def _transliterate(ctx: InjectContext, word: str, lang: str) -> str:
    if ctx.bodhan is None or not transliterable(lang):
        return ""
    try:
        return ctx.bodhan.transliterate(word, lang).strip()
    except (BodhanError, ServiceUnavailable, BudgetExceeded):
        return ""


def located_entities(it: Item, ctx: InjectContext) -> list[tuple[str, int, int]]:
    """Source names/terms found in the translation: kept in Latin, or (with models) through their transliteration."""
    t = it.translation or ""
    toks = word_tokens(t)
    folded = [normalize_text(x.text, asr_fold=True) for x in toks]
    min_sim = defaults()["check_params"]["entity_match_similarity"]
    out = []
    for word, _, _ in source_entities(it.source):
        m = re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", t, re.IGNORECASE)
        if m:
            out.append((word, m.start(), m.end()))
            continue
        native = normalize_text(_transliterate(ctx, word, it.lang), asr_fold=True)
        if native:
            score, i, j = best_window_match(native, folded)
            if score >= min_sim:
                out.append((word, toks[i].start, toks[j].end))
    return out


def entity_swap(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Replace a name with another item's name (in Latin if the original is in Latin, else its transliteration)."""
    found = located_entities(it, ctx)
    own = {w for w, _, _ in source_entities(it.source)}
    pool = sorted({w for o in ctx.items if o.id != it.id for w, _, _ in source_entities(o.source)} - own)
    if not found or not pool:
        return None
    _, a, b = rng.choice(found)
    t = it.translation or ""
    other = rng.choice(pool)
    rendering = other if t[a:b].isascii() else _transliterate(ctx, other, it.lang)
    if not rendering or rendering == t[a:b]:
        return None
    new, span = replace(t, a, b, rendering)
    return Edit(span=span, translation=new)


def garble_entity(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Swap two adjacent letters (grapheme clusters) inside a name: a misspelt transliteration."""
    t = it.translation or ""
    cands = []
    for _, a, b in located_entities(it, ctx):
        units = [(a + x, a + y) for x, y in clusters(t[a:b])]
        for k in range(1, len(units) - 2):
            (p, q), (r, s) = units[k], units[k + 1]
            if t[p:q] != t[r:s] and not (t[p:q] + t[r:s]).isspace() and " " not in t[p:s]:
                cands.append((a, b, p, q, s))
    if not cands:
        return None
    a, b, p, q, s = rng.choice(cands)
    new = t[:p] + t[q:s] + t[p:q] + t[s:]
    return Edit(span=(a, b), translation=new)


# ---------------------------------------------------------------------------
# format
# ---------------------------------------------------------------------------


def heading_marker(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Drop a heading's ``#`` marks: the heading renders as plain text."""
    t = it.translation or ""
    heads = [ln for ln in markup.md_lines(t) if ln.kind.startswith("heading")]
    if not heads:
        return None
    ln = rng.choice(heads)
    m = re.match(r"\s*#{1,6}\s+", t[ln.start : ln.end])
    assert m is not None
    new = t[: ln.start] + t[ln.start + m.end() :]
    return Edit(span=(ln.start, ln.end - m.end()), translation=new)


BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")


def bold_marker(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Remove the closing ``**`` of a bold phrase: the bold no longer closes."""
    t = it.translation or ""
    protected = markup.protected_spans(t)
    pairs = [m for m in BOLD_RE.finditer(t) if not markup.in_spans(m.start(), protected)]
    if not pairs:
        return None
    m = rng.choice(pairs)
    close = m.end() - 2
    return Edit(span=(m.start(), close), translation=t[:close] + t[m.end() :])


def table_pipe(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Remove one inner ``|`` from a table body row: the row has one column too few."""
    t = it.translation or ""
    lines = markup.md_lines(t)
    cands = []
    for prev, ln in zip(lines, lines[1:]):
        if ln.kind == "table" and prev.kind in ("table", "table_sep"):
            row = t[ln.start : ln.end]
            first, last = len(row) - len(row.lstrip()), len(row.rstrip()) - 1
            cands += [ln.start + k for k, c in enumerate(row) if c == "|" and first < k < last]
    if not cands:
        return None
    k = rng.choice(cands)
    ln = next(x for x in lines if x.start <= k < x.end)
    return Edit(span=(ln.start, ln.end - 1), translation=t[:k] + t[k + 1 :])


def table_separator(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Delete a table's ``|---|`` separator row: the table no longer renders. Labelled on the header row."""
    t = it.translation or ""
    lines = markup.md_lines(t)
    cands = [(prev, ln) for prev, ln in zip(lines, lines[1:]) if ln.kind == "table_sep" and prev.kind == "table"]
    if not cands:
        return None
    header, sep = rng.choice(cands)
    new = t[: sep.start] + t[sep.end + 1 :] if sep.end < len(t) else t[: sep.start - 1]
    return Edit(span=(header.start, header.end), translation=new)


def math_dollar(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Remove the closing ``$`` of an inline formula: the formula no longer closes."""
    t = it.translation or ""
    segs = [s for s in markup.math_segments(t) if s.kind == "d"]
    if not segs:
        return None
    s = rng.choice(segs)
    return Edit(span=(s.start, s.end - 1), translation=t[: s.end - 1] + t[s.end :])


# a swap keeps the formula valid but changes what it says; other commands are misspelt (they stop rendering)
LATEX_SWAPS = {
    "\\times": "\\div",
    "\\div": "\\times",
    "\\sin": "\\cos",
    "\\cos": "\\sin",
    "\\tan": "\\cot",
    "\\leq": "\\geq",
    "\\geq": "\\leq",
    "\\le": "\\ge",
    "\\ge": "\\le",
    "\\pi": "\\phi",
    "\\alpha": "\\beta",
    "\\beta": "\\alpha",
    "\\sum": "\\prod",
    "\\log": "\\ln",
    "\\ln": "\\log",
}
LATEX_STRUCTURE = {"\\text", "\\textrm", "\\textbf", "\\mbox", "\\begin", "\\end", "\\left", "\\right"}


def latex_command(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Change a LaTeX command: swap it for one that means something else (\\times -> \\div) or misspell it."""
    t = it.translation or ""
    cands = []
    for seg in markup.math_segments(t):
        for m in re.finditer(r"\\[A-Za-z]+", t[seg.start : seg.end]):
            name = m.group(0)
            new_name = LATEX_SWAPS.get(name) or (name[:-1] if len(name) > 3 else None)
            if name not in LATEX_STRUCTURE and new_name:
                cands.append((seg.start + m.start(), seg.start + m.end(), new_name))
    if not cands:
        return None
    a, b, new_name = rng.choice(cands)
    new, span = replace(t, a, b, new_name)
    return Edit(span=span, translation=new)


# ---------------------------------------------------------------------------
# wrong language
# ---------------------------------------------------------------------------


def source_english(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """The whole output is the English source, untranslated."""
    if it.translation == it.source:
        return None
    return Edit(span=(0, len(it.source)), translation=it.source)


def english_sentence(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """One sentence is left in English: the aligned English sentence replaces its translation."""
    t = it.translation or ""
    prose = set(prose_segments(t))
    cands = [(s, tgt) for s, tgt in aligned(it) or [] if tgt in prose and len(word_tokens(it.source[s[0] : s[1]])) >= 4]
    if not cands:
        return None
    (s0, s1), (a, b) = rng.choice(cands)
    new, span = replace(t, a, b, it.source[s0:s1])
    return Edit(span=span, translation=new)


def other_language(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """The output is this item's translation into another language (e.g. Tamil text for a Hindi item)."""
    others = [
        o for o in ctx.by_id.get(it.id, []) if o.lang != it.lang and o.translation and o.translation != it.translation
    ]
    if not others:
        return None
    o = rng.choice(others)
    assert o.translation is not None
    return Edit(span=(0, len(o.translation)), translation=o.translation, note=f"text from the {o.lang} item")
