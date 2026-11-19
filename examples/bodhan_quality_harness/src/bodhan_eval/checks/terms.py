"""Term and entity preservation.

Acronyms, product names and glossary terms are the highest-signal entities in
technical content, and a translator that drops or mangles them is easy to catch
without any model call.
"""

from __future__ import annotations

import re

from ..normalize import latin_tokens, normalize_loose
from ..spans import find_all, span_preserving
from .base import Flag, ItemContext

# Tokens that are noise rather than entities.
STOP_LATIN = {
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "is", "are", "was",
    "for", "with", "as", "by", "at", "it", "this", "that", "from", "be",
}


def _entity_tokens(text: str) -> set[str]:
    """Latin tokens that look like entities: acronyms, CamelCase, digits, dots."""
    out = set()
    for tok in latin_tokens(text):
        if tok.lower() in STOP_LATIN or len(tok) < 2:
            continue
        # A trailing dot is punctuation, not a signal. Require a real marker:
        # all-caps, a digit, an internal capital (CamelCase), or an acronym shape.
        # Proper names (Chandrayaan, Ganga) are transliterated, not preserved,
        # so flagging them would be a false alarm. Only technical shapes count:
        # acronyms, CamelCase with an internal capital, or mixed alphanumerics.
        acronym = tok.isupper() and len(tok) >= 2
        camel = any(c.isupper() for c in tok[1:]) and not tok.isupper()
        alnum = any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)
        proper_name = tok[0].isupper() and tok[1:].islower()
        # Mixed-case tokens like "NaCl" are transliterated by Indic-Translate
        # ("என்.ஏ.எல்.சி."), so treating them as must-preserve entities is a false
        # alarm. Only all-caps acronyms and tokens containing digits survive verbatim.
        looks_entity = (acronym or alnum) and not proper_name
        if looks_entity:
            out.add(tok)
    return out


def check_terms(ctx: ItemContext) -> list[Flag]:
    if not ctx.translation:
        return []
    entities = _entity_tokens(ctx.source)
    if not entities:
        return []

    hay = normalize_loose(ctx.translation)
    flags: list[Flag] = []
    for ent in sorted(entities):
        if normalize_loose(ent) not in hay:
            flags.append(Flag(
                category="term", severity="major", span=None,
                evidence=f"entity/acronym '{ent}' from the source is not present in the translation",
                check="terms.entity_missing", confidence=0.6,
            ))
    return flags


def check_glossary(ctx: ItemContext) -> list[Flag]:
    """Glossary terms that must be rendered exactly as specified.

    ``ctx.extra['glossary']`` maps source term -> required target rendering.
    """
    glossary: dict[str, str] = ctx.extra.get("glossary") or {}
    if not ctx.translation or not glossary:
        return []
    flags: list[Flag] = []
    tgt_view = span_preserving(ctx.translation)
    for src_term, want in glossary.items():
        if normalize_loose(src_term) not in normalize_loose(ctx.source):
            continue
        if normalize_loose(want) in normalize_loose(ctx.translation):
            continue
        hits = find_all(re.compile(re.escape(want)), tgt_view)
        span = (hits[0][1], hits[0][2]) if hits else None
        flags.append(Flag(
            category="term", severity="major", span=span,
            evidence=f"glossary term '{src_term}' should render as '{want}'",
            check="terms.glossary", confidence=0.8,
        ))
    return flags
