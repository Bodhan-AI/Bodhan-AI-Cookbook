"""Text error injections on a translation. Each returns the modified translation and a label
with the exact character span of the error, or None if the item does not allow that kind.

No model writes Indian-language text here: errors are made by editing the system's own output
(delete, insert, change digits, break markup, restore English), or by perturbing the English
source and asking Indic-Translate to re-translate one sentence (`--retranslate`, paid).
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from bodhan_eval.checks.markup import md_blocks
from bodhan_eval.checks.translation import source_entities
from bodhan_eval.normalize import extract_numbers, normalize_text, split_sentences


@dataclass
class Injection:
    translation: str
    label: dict[str, Any]


@dataclass
class Ctx:
    source: str
    translation: str
    lang: str
    translate_code: str
    pool: list[str]  # sentences from other items' translations in the same language
    entity_pool: list[str]  # entities from other items' sources


class Translator(Protocol):
    async def translate(self, text: str, target_language: str, script: str = ...): ...


def _label(cat: str, sev: str, span: tuple[int, int], kind: str, **extra) -> dict[str, Any]:
    return {
        "type": cat,
        "severity": sev,
        "span": list(span),
        "annotator": "injector",
        "injection": kind,
        **extra,
    }


def _aligned(ctx: Ctx) -> tuple[list[tuple[int, int]], list[tuple[int, int]]] | None:
    """Sentence spans of source and translation, only if they correspond one to one."""
    s, t = split_sentences(ctx.source), split_sentences(ctx.translation)
    return (s, t) if len(s) == len(t) and len(s) >= 1 else None


def _splice(text: str, span: tuple[int, int], new: str) -> tuple[str, tuple[int, int]]:
    return text[: span[0]] + new + text[span[1] :], (span[0], span[0] + len(new))


# --- programmatic -----------------------------------------------------------------------


def omission(ctx: Ctx, rng: random.Random) -> Injection | None:
    """Delete one translation sentence. The label marks the deletion point (zero width),
    with the deleted content's source span when sentences align."""
    t = split_sentences(ctx.translation)
    if len(t) < 2:
        return None
    i = rng.randrange(len(t))
    s, e = t[i]
    if i + 1 < len(t):
        e = t[i + 1][0]  # also remove the whitespace up to the next sentence
    elif i > 0:
        s = t[i - 1][1]
    new = ctx.translation[:s] + ctx.translation[e:]
    al = _aligned(ctx)
    extra = {"source_span": list(al[0][i])} if al else {}
    return Injection(new, _label("omission", "major", (s, s), "delete_sentence", **extra))


def number(ctx: Ctx, rng: random.Random) -> Injection | None:
    """Change one digit of a number that the source also contains (keeps its digit system)."""
    src_vals = {n.value for n in extract_numbers(ctx.source)}
    cands = [n for n in extract_numbers(ctx.translation) if n.value in src_vals]
    if not cands:
        return None
    n = rng.choice(cands)
    digit_pos = [k for k in range(n.start, n.end) if ctx.translation[k].isdecimal()]
    k = digit_pos[-1]
    ch = ctx.translation[k]
    d = unicodedata.decimal(ch)
    nd = (d + rng.randint(1, 9)) % 10
    if len(digit_pos) == 1 and nd == 0:
        nd = (d + 1) % 10 or 1
    new_ch = chr(ord(ch) - d + nd)  # same Unicode digit block
    new = ctx.translation[:k] + new_ch + ctx.translation[k + 1 :]
    return Injection(new, _label("number", "major", (n.start, n.end), "change_digit"))


def addition(ctx: Ctx, rng: random.Random) -> Injection | None:
    """Insert a sentence taken from another item in the same language."""
    t = split_sentences(ctx.translation)
    pool = [p for p in ctx.pool if p.strip() and p not in ctx.translation]
    if not t or not pool:
        return None
    sent = rng.choice(pool).strip()
    s, e = rng.choice(t)
    pos = e
    new = ctx.translation[:pos] + " " + sent + ctx.translation[pos:]
    return Injection(
        new, _label("addition", "major", (pos + 1, pos + 1 + len(sent)), "insert_sentence")
    )


def wrong_language(ctx: Ctx, rng: random.Random) -> Injection | None:
    """Put the English source back in place of one sentence (or, rarely, the whole text)."""
    al = _aligned(ctx)
    # Only sentences with words that the translation actually changed (not a table rule
    # "|---|" or a number-only cell, whose "English" is identical).
    cands = []
    if al:
        for i, ((a, b), (c, d)) in enumerate(zip(al[0], al[1], strict=True)):
            eng, tgt = ctx.source[a:b], ctx.translation[c:d]
            if any(ch.isalpha() for ch in eng) and normalize_text(eng) != normalize_text(tgt):
                cands.append(i)
    if len(cands) >= 2 and rng.random() < 0.8:
        i = rng.choice(cands)
        eng = ctx.source[al[0][i][0] : al[0][i][1]]
        new, span = _splice(ctx.translation, al[1][i], eng)
        return Injection(new, _label("wrong_language", "major", span, "untranslated_sentence"))
    return Injection(
        ctx.source,
        _label("wrong_language", "critical", (0, len(ctx.source)), "untranslated_text"),
    )


_INLINE_MATH = re.compile(r"(?<![\\$])\$(?!\$)([^$\n]+?)\$(?!\$)")


def format_break(ctx: Ctx, rng: random.Random) -> Injection | None:
    """Break Markdown / LaTeX structure that the translation contains."""
    tr = ctx.translation
    options: list[tuple[str, Any]] = []
    for b in md_blocks(tr):
        if b.kind == "table_row" and tr[b.start : b.end].count("|") >= 3:
            options.append(("table", b))
        elif b.kind.startswith("h") and b.kind[1:].isdigit():
            options.append(("heading", b))
    options += [("math", m) for m in _INLINE_MATH.finditer(tr)]
    options += [("bold", m) for m in re.finditer(r"\*\*[^*\n]+\*\*", tr)]
    if not options:
        return None
    kind, obj = rng.choice(options)
    if kind == "table":
        row = tr[obj.start : obj.end]
        pipes = [k for k, c in enumerate(row) if c == "|"][1:-1] or [row.index("|")]
        k = obj.start + rng.choice(pipes)
        new, span = tr[:k] + tr[k + 1 :], (obj.start, obj.end - 1)
    elif kind == "heading":
        line = tr[obj.start : obj.end]
        stripped = line.lstrip("#").lstrip()
        new = tr[: obj.start] + stripped + tr[obj.end :]
        span = (obj.start, obj.start + len(stripped))
    elif kind == "math":
        s, e = obj.span()
        new, span = tr[: e - 1] + tr[e:], (s, e - 1)  # drop the closing $
    else:
        s, e = obj.span()
        new, span = tr[: e - 2] + tr[e:], (s, e - 2)  # drop the closing **
    return Injection(new, _label("format", "major", span, f"break_{kind}"))


PROGRAMMATIC = {
    "omission": omission,
    "number": number,
    "addition": addition,
    "wrong_language": wrong_language,
    "format": format_break,
}

# --- perturb English, re-translate one sentence (paid) ------------------------------------

_ANTONYMS = [
    ("increase", "decrease"), ("increases", "decreases"), ("increased", "decreased"),
    ("more", "less"), ("higher", "lower"), ("larger", "smaller"), ("before", "after"),
    ("north", "south"), ("east", "west"), ("hot", "cold"), ("always", "never"),
    ("first", "last"), ("early", "late"), ("open", "closed"), ("rich", "poor"),
    ("strong", "weak"), ("faster", "slower"), ("most", "least"), ("above", "below"),
]  # fmt: skip
_NEGATABLE = re.compile(r"\b(is|are|was|were|can|will|should|does|do|did|has|have)\b( not\b)?")
_FALLBACK_ENTITIES = ["Germany", "Mumbai", "Einstein", "NASA", "Tokyo", "Amazon", "Kerala"]


def perturb_meaning(sentence: str, rng: random.Random) -> str | None:
    """Flip the meaning of an English sentence: swap a word for its antonym, or add / remove
    a negation. Uses whichever is possible (randomly, when both are)."""
    words = {a: b for a, b in _ANTONYMS} | {b: a for a, b in _ANTONYMS}
    hits = [m for m in re.finditer(r"\b[A-Za-z]+\b", sentence) if m.group(0).lower() in words]
    neg = list(_NEGATABLE.finditer(sentence))
    if hits and (not neg or rng.random() < 0.6):
        m = rng.choice(hits)
        rep = words[m.group(0).lower()]
        rep = rep.capitalize() if m.group(0)[0].isupper() else rep
        return sentence[: m.start()] + rep + sentence[m.end() :]
    if neg:
        m = rng.choice(neg)
        if m.group(2):  # remove an existing "not"
            return sentence[: m.end(1)] + sentence[m.end() :]
        return sentence[: m.end(1)] + " not" + sentence[m.end(1) :]
    return None


async def _retranslate_sentence(
    client: Translator, ctx: Ctx, i: int, new_src: str, t_spans: list[tuple[int, int]]
) -> tuple[str, tuple[int, int]] | None:
    res = await client.translate(new_src, ctx.translate_code)
    if res.truncated or not res.text.strip():
        return None
    old = ctx.translation[t_spans[i][0] : t_spans[i][1]]
    if normalize_text(res.text) == normalize_text(old):
        return None  # the change did not survive translation
    return _splice(ctx.translation, t_spans[i], res.text.strip())


async def mistranslation(ctx: Ctx, rng: random.Random, client: Translator) -> Injection | None:
    al = _aligned(ctx)
    if not al:
        return None
    s_spans, t_spans = al
    order = list(range(len(s_spans)))
    rng.shuffle(order)
    for i in order:
        src = ctx.source[s_spans[i][0] : s_spans[i][1]]
        new_src = perturb_meaning(src, rng)
        if not new_src:
            continue
        out = await _retranslate_sentence(client, ctx, i, new_src, t_spans)
        if out:
            return Injection(
                out[0],
                _label(
                    "mistranslation",
                    "major",
                    out[1],
                    "perturb_retranslate",
                    source_span=list(s_spans[i]),
                    perturbed_source=new_src,
                ),
            )
    return None


_NOT_NAMES = {"celsius", "fahrenheit", "kelvin"}  # capitalised units, not entities


def _swappable(source: str, ent: str, span: tuple[int, int]) -> bool:
    """A real name to swap: not a unit, not a table header / heading word (capitalised by
    layout, not because it is a name)."""
    if ent.lower() in _NOT_NAMES:
        return False
    for b in md_blocks(source):
        if b.start <= span[0] < b.end and (b.kind == "table_row" or b.kind.startswith("h")):
            first_row = not any(
                o.kind == "table_row" and o.start < b.start for o in md_blocks(source)
            )
            if b.kind != "table_row" or first_row:
                return False
    return True


async def term(ctx: Ctx, rng: random.Random, client: Translator) -> Injection | None:
    al = _aligned(ctx)
    if not al:
        return None
    s_spans, t_spans = al
    ents = [(e, sp) for e, sp in source_entities(ctx.source) if _swappable(ctx.source, e, sp)]
    rng.shuffle(ents)
    for ent, (s, e) in ents:
        i = next((k for k, (a, b) in enumerate(s_spans) if a <= s < b), None)
        if i is None:
            continue
        choices = [x for x in (ctx.entity_pool or _FALLBACK_ENTITIES) if x.lower() != ent.lower()]
        if not choices:
            continue
        rep = rng.choice(choices)
        a, b = s_spans[i]
        new_src = ctx.source[a:s] + rep + ctx.source[e:b]
        out = await _retranslate_sentence(client, ctx, i, new_src, t_spans)
        if out:
            return Injection(
                out[0],
                _label(
                    "term",
                    "major",
                    out[1],
                    "swap_entity_retranslate",
                    source_span=[s, e],
                    replaced=ent,
                    replacement=rep,
                ),
            )
    return None


RETRANSLATE = {"mistranslation": mistranslation, "term": term}
