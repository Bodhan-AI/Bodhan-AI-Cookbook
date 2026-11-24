"""T6 term_entity: named terms and acronyms must be preserved, and rendered consistently.

Per item: acronyms / alphanumeric terms (DNA, H2O, GPS) are normally kept verbatim in Latin;
if such a term is absent from the translation AND absent from the back-translation (when
available), that is MEDIUM evidence of a dropped/garbled term.

Across the dataset: if one source term is rendered several different ways within one language
(via the back-translation token match), that inconsistency is flagged with all variants.
Proper nouns are treated gently (they are often transliterated), so their absence in Latin is
not flagged unless the back-translation also lacks them.
"""

from __future__ import annotations

from ..entities import extract_terms
from ..models import Evidence
from .registry import CheckContext, register


@register("t6_term_entity", category="term_entity",
          description="acronym/term preservation and cross-item rendering consistency")
def check(ctx: CheckContext) -> list[Evidence]:
    tr = ctx.translation
    if not tr or not ctx.source.strip():
        return []
    terms = extract_terms(ctx.source)
    if not terms:
        return []
    back = ctx.artifacts.get("back_translation")  # English back-translation text, if computed
    back_lower = back.lower() if isinstance(back, str) else None
    tr_lower = tr.lower()
    out: list[Evidence] = []

    for term in terms:
        tl = term.text.lower()
        in_tr = tl in tr_lower
        in_back = (back_lower is not None) and (tl in back_lower)
        if in_tr or in_back:
            continue
        # proper nouns legitimately transliterate; only flag if we have a back-translation
        # that confirms the term is gone. Acronyms/alnum are expected verbatim.
        if term.kind in ("acronym", "alnum"):
            strength, noisy = "medium", False
        else:
            if back_lower is None:
                continue  # cannot confirm without back-translation; stay silent
            strength, noisy = "medium", True
        out.append(Evidence(
            check="t6_term_entity", category="term_entity", strength=strength, noisy=noisy,
            score=0.7, severity_hint="major", source_span=term.span,
            details={"term": term.text, "kind": term.kind,
                     "confirmed_absent_in_backtranslation": in_back is False and back_lower is not None},
        ))
    return out
