"""``--with-models`` edits: change the *English*, re-translate with Indic-Translate, splice the result in.

The rules forbid any model writing Indian-language text except Bodhan's own (CLAUDE.md §4.1, §4.4). So a
meaning error is made in English, where a fixed word list can make it precisely ("expands" -> "contracts"),
and Indic-Translate turns it into Indian-language text, exactly as it would turn a faulty source into a faulty
translation. Only one sentence is touched:

1. the source and the translation must split into the same number of sentences (``base.aligned``), so the
   i-th English sentence corresponds to the i-th translated one;
2. the chosen English sentence is edited and translated alone, and so is the unedited sentence;
3. the edited sentence's translation replaces its counterpart, and the label covers the words where the two
   single-sentence translations differ (``diff_span``).

The rest of the translation is untouched, so the label is the only change of meaning in the item.
"""

from __future__ import annotations

import difflib
import random
import re
from collections.abc import Callable

from ..budget import BudgetExceeded
from ..clients import BodhanError, ServiceUnavailable
from ..models import Item
from ..normalize import ws_tokens
from .base import CLOSERS, Edit, InjectContext, Span, aligned, prose_segments, sentence_body_end

EnglishEdits = Callable[[str], list[tuple[int, int, str]]]  # sentence -> [(start, end, replacement)]


def _pairs(*pairs: tuple[str, str]) -> dict[str, str]:
    """Both directions of each word pair."""
    out = {}
    for a, b in pairs:
        out[a], out[b] = b, a
    return out


ANTONYMS = _pairs(
    ("increase", "decrease"), ("increases", "decreases"), ("increased", "decreased"), ("rise", "fall"),
    ("rises", "falls"), ("expand", "contract"), ("expands", "contracts"), ("absorb", "release"),
    ("absorbs", "releases"), ("gains", "loses"), ("attracts", "repels"), ("heavier", "lighter"),
    ("larger", "smaller"), ("largest", "smallest"), ("higher", "lower"), ("highest", "lowest"),
    ("longer", "shorter"), ("longest", "shortest"), ("faster", "slower"), ("hot", "cold"), ("warm", "cool"),
    ("strong", "weak"), ("more", "less"), ("maximum", "minimum"), ("positive", "negative"), ("north", "south"),
    ("east", "west"), ("melts", "freezes"), ("early", "late"), ("dry", "wet"), ("add", "subtract"),
    ("multiply", "divide"), ("import", "export"), ("inhale", "exhale"),
)  # fmt: skip
RELATIONS = _pairs(
    ("above", "below"), ("before", "after"), ("inside", "outside"), ("under", "over"), ("top", "bottom"),
    ("first", "last"), ("upstream", "downstream"), ("internal", "external"),
)  # fmt: skip
QUANTIFIERS = {  # one direction: each weakens or changes the claim a little
    "all": "most", "always": "often", "every": "some", "never": "rarely", "many": "a few", "none": "few",
    "each": "some", "most": "some",
}  # fmt: skip
UNITS = {  # applied only right after a number
    "km": "m", "m": "km", "kg": "g", "g": "kg", "cm": "mm", "mm": "cm", "°C": "°F", "°F": "°C",
    "kilometres": "metres", "kilometers": "meters", "metres": "kilometres", "meters": "kilometers",
    "kilograms": "grams", "grams": "kilograms", "litres": "millilitres", "liters": "milliliters",
    "hours": "minutes", "hour": "minute", "minutes": "seconds", "minute": "second", "seconds": "minutes",
    "years": "months", "days": "weeks",
}  # fmt: skip
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]  # fmt: skip
EXTRA_CLAUSES = [
    ", which surprised many scientists at the time",
    ", as most textbooks of the last century also said",
    ", especially in the northern states",
    ", according to a recent survey of farmers",
]


def _same_case(word: str, repl: str) -> str:
    return repl[:1].upper() + repl[1:] if word[:1].isupper() else repl


def _word_swaps(table: dict[str, str]) -> EnglishEdits:
    def edits(sentence: str) -> list[tuple[int, int, str]]:
        out = []
        for m in re.finditer(r"[A-Za-z]+", sentence):
            repl = table.get(m.group(0).lower())
            if repl:
                out.append((m.start(), m.end(), _same_case(m.group(0), repl)))
        return out

    return edits


def _negations(sentence: str) -> list[tuple[int, int, str]]:
    """Remove a negation (``is not`` -> ``is``, ``cannot`` -> ``can``) or add one (``is`` -> ``is not``)."""
    out = []
    aux = r"is|are|was|were|will|should|must|does|do|did|has|have|had|can"
    for m in re.finditer(rf"\b(?:{aux})( not|n't)\b", sentence, re.IGNORECASE):
        out.append((m.start(1), m.end(1), ""))
    for m in re.finditer(r"\bcannot\b", sentence, re.IGNORECASE):
        out.append((m.start(), m.end(), _same_case(m.group(0), "can")))
    for m in re.finditer(r"\b(?:is|are|was|were|will|should|must)\b(?! not)(?!n't)", sentence, re.IGNORECASE):
        out.append((m.end(), m.end(), " not"))
    for m in re.finditer(r"\bcan\b(?! not)", sentence, re.IGNORECASE):
        out.append((m.start(), m.end(), _same_case(m.group(0), "cannot")))
    return out


_UNIT_RE = re.compile(
    r"(?<=\d)(\s?)(" + "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True)) + r")(?![A-Za-z])"
)


def _units(sentence: str) -> list[tuple[int, int, str]]:
    return [(m.start(2), m.end(2), UNITS[m.group(2)]) for m in _UNIT_RE.finditer(sentence)]


def _months(sentence: str) -> list[tuple[int, int, str]]:
    """Move a month name three months on (March -> June)."""
    return [
        (m.start(), m.end(), MONTHS[(MONTHS.index(m.group(0)) + 3) % 12])
        for m in re.finditer(r"\b(?:" + "|".join(MONTHS) + r")\b", sentence)
    ]


def _extra_clause(sentence: str) -> list[tuple[int, int, str]]:
    end = sentence_body_end(sentence)
    if end == len(sentence) or end < 10:
        return []
    return [(end, end, clause) for clause in EXTRA_CLAUSES]


# ---------------------------------------------------------------------------
# the shared mechanism
# ---------------------------------------------------------------------------


def _translate(ctx: InjectContext, text: str, lang: str) -> str:
    assert ctx.bodhan is not None
    try:
        return str(ctx.bodhan.translate(text, lang)["translation"]).strip()
    except (BodhanError, ServiceUnavailable, BudgetExceeded):
        return ""


def diff_span(old: str, new: str) -> Span:
    """The stretch of ``new`` covering every word that differs from ``old`` (edge punctuation trimmed).

    If words were only deleted, the word right after the gap is marked, so the label is never empty.
    """
    a_t, b_t = ws_tokens(old), ws_tokens(new)
    sm = difflib.SequenceMatcher(a=[x.text for x in a_t], b=[x.text for x in b_t], autojunk=False)
    changed = [(j1, j2) for tag, _, _, j1, j2 in sm.get_opcodes() if tag != "equal"]
    j1, j2 = min(j for j, _ in changed), max(j for _, j in changed)
    if j2 <= j1:
        j1 = min(j1, len(b_t) - 1)
        j2 = j1 + 1
    a, b = b_t[j1].start, b_t[j2 - 1].end
    while a < b and new[a] in CLOSERS + "(":
        a += 1
    while b > a and new[b - 1] in CLOSERS + "(":
        b -= 1
    return a, b


def retranslate_one(it: Item, rng: random.Random, ctx: InjectContext, edits: EnglishEdits) -> Edit | None:
    """Edit one aligned English sentence, translate it alone, and put it in place of its translation."""
    if ctx.bodhan is None or not it.translation:
        return None
    t = it.translation
    prose = set(prose_segments(t))
    options = [(s, tgt, e) for s, tgt in aligned(it) or [] if tgt in prose for e in edits(it.source[s[0] : s[1]])]
    if not options:
        return None
    (s0, s1), (t0, t1), (a, b, repl) = rng.choice(options)
    sentence = it.source[s0:s1]
    edited = sentence[:a] + repl + sentence[b:]
    before, after = _translate(ctx, sentence, it.lang), _translate(ctx, edited, it.lang)
    if not before or not after or before == after:
        return None  # Indic-Translate failed or ignored the edit: nothing to label
    da, db = diff_span(before, after)
    return Edit(
        span=(t0 + da, t0 + db),
        translation=t[:t0] + after + t[t1:],
        note=f"English edit: {sentence[a:b]!r} -> {repl!r}",
    )


def negation(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Add or remove a negation: the sentence says the opposite."""
    return retranslate_one(it, rng, ctx, _negations)


def antonym_swap(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Swap a word for its antonym (expands -> contracts)."""
    return retranslate_one(it, rng, ctx, _word_swaps(ANTONYMS))


def relation_swap(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Swap a relation word (above -> below, before -> after)."""
    return retranslate_one(it, rng, ctx, _word_swaps(RELATIONS))


def quantifier_swap(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Weaken a quantifier (all -> most, always -> often)."""
    return retranslate_one(it, rng, ctx, _word_swaps(QUANTIFIERS))


def unit_swap(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Change the unit after a number (km -> m, hours -> minutes)."""
    return retranslate_one(it, rng, ctx, _units)


def month_swap(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Change a month name (March -> June)."""
    return retranslate_one(it, rng, ctx, _months)


def extra_clause(it: Item, rng: random.Random, ctx: InjectContext) -> Edit | None:
    """Add a short clause the source does not have."""
    return retranslate_one(it, rng, ctx, _extra_clause)
