"""Back-translation checks (English vs English), plus entity preservation.

The back-translation is produced by indic-translate itself, so it is a noisy witness. Clause
coverage is measured with stemmed content words and fuzzy matching, and every score is
scaled down when the round trip as a whole looks unreliable (low global coverage), which is
how checker noise is kept from turning into flags.
"""

from __future__ import annotations

import regex

from ..config import Thresholds
from ..models import Category, Severity, Signal
from ..text.align import similarity
from ..text.segment import EN_STOPWORDS, Piece, clauses, content_words, sentences, stem, words
from .base import ItemContext
from .textual import chrf

ENTITY_RE = regex.compile(
    r"\b(?:[A-Z][\p{L}'\-]*[a-z0-9][\p{L}'\-]*|[A-Z]{2,}[0-9]*|[A-Z][a-z]*[0-9]+[A-Za-z0-9\-]*)(?:\s+(?:of\s+|de\s+|the\s+)?(?:[A-Z][\p{L}'\-]+|[A-Z]{2,}))*"
)


def _stems(text: str) -> list[str]:
    return [stem(w.text) for w in content_words(text)]


def _covered(term: str, pool: set[str]) -> bool:
    if term in pool:
        return True
    return len(term) >= 4 and any(len(p) >= 4 and similarity(term, p) >= 0.8 for p in pool)


def coverage(text: str, pool: set[str]) -> tuple[float, int]:
    stems = _stems(text)
    if not stems:
        return 1.0, 0
    return sum(1 for s in stems if _covered(s, pool)) / len(stems), len(stems)


def source_entities(source: str) -> list[Piece]:
    starts = {s.start for s in sentences(source)} | {m.end() for m in regex.finditer(r"^\s*(?:[-*+]|\d+[.)]|#+|\|)?\s*", source, regex.MULTILINE)}
    out = []
    for m in ENTITY_RE.finditer(source):
        text = m.group(0).strip()
        first = text.split()[0]
        at_start = m.start() in starts or (m.start() > 0 and source[m.start() - 1] in "\"'(“")
        multi = len(text.split()) > 1
        acronym = first.isupper() and len(first) >= 2
        has_digit = any(ch.isdigit() for ch in text)
        if at_start and not (multi or acronym or has_digit):
            continue
        if text.lower() in EN_STOPWORDS:
            continue
        out.append(Piece(text, m.start(), m.end()))
    return out


def _found_fuzzy(entity: str, text: str) -> bool:
    target = entity.casefold()
    tokens = [w.text.casefold() for w in words(text)]
    n = max(len(entity.split()), 1)
    for size in {n, max(n - 1, 1), n + 1}:
        for i in range(0, max(len(tokens) - size + 1, 0)):
            if similarity(target, " ".join(tokens[i : i + size])) >= 0.75:
                return True
    return False


def check_backtranslation(ctx: ItemContext, th: Thresholds) -> None:
    bt = ctx.back_translation
    with ctx.check("back_translation", "roundtrip") as res:
        if not bt:
            res.status = "skipped"
            res.reason = "no back-translation available"
            return
        bt_pool = set(_stems(bt))
        src_pool = set(_stems(ctx.source))
        global_cov, n_src = coverage(ctx.source, bt_pool)
        reverse_cov, _ = coverage(bt, src_pool)
        bt_chrf = chrf(bt, ctx.source)
        reliability = min(1.0, max(0.3, (global_cov - 0.3) / 0.5))
        res.metrics = {
            "chrf++_vs_source": bt_chrf,
            "source_coverage": round(global_cov, 3),
            "bt_coverage": round(reverse_cov, 3),
            "reliability": round(reliability, 3),
        }

        for clause in clauses(ctx.source):
            cov, n = coverage(clause.text, bt_pool)
            if n >= 3 and cov < th.backtranslation_clause_coverage:
                share = len(clause.text) / max(len(ctx.source), 1)
                ctx.add(
                    Signal(
                        check="back_translation",
                        category=Category.OMISSION,
                        score=round(0.55 * reliability, 3),
                        severity=Severity.CRITICAL if share > 0.5 else Severity.MAJOR,
                        source_span=(clause.start, clause.end),
                        message=f"source clause barely appears in the back-translation (coverage {cov:.0%})",
                        data={"coverage": cov, "clause": clause.text},
                    )
                )

        bt_sents, tgt_sents = sentences(bt), sentences(ctx.translation or "")
        for idx, clause in enumerate(bt_sents):
            cov, n = coverage(clause.text, src_pool)
            if n >= 4 and cov < 0.2:
                span = (tgt_sents[idx].start, tgt_sents[idx].end) if len(bt_sents) == len(tgt_sents) else None
                ctx.add(
                    Signal(
                        check="back_translation",
                        category=Category.ADDITION,
                        score=round(0.5 * reliability, 3),
                        severity=Severity.MAJOR,
                        span=span,
                        message=f"back-translated sentence has content not in the source: {clause.text!r}",
                        data={"coverage": cov},
                    )
                )

        for ent in source_entities(ctx.source):
            if _found_fuzzy(ent.text, bt):
                continue
            ctx.add(
                Signal(
                    check="back_translation",
                    category=Category.TERM,
                    score=round(0.4 * reliability, 3),
                    severity=Severity.MAJOR,
                    source_span=(ent.start, ent.end),
                    message=f"name or term {ent.text!r} does not survive the round trip",
                    data={"entity": ent.text},
                )
            )


def check_latin_terms(ctx: ItemContext) -> None:
    """A Latin-script term kept in the translation should match the source spelling exactly."""
    translation = ctx.translation or ""
    with ctx.check("latin_terms", "deterministic") as res:
        if ctx.lang.script == "Latn":
            res.status = "skipped"
            res.reason = "target is written in Latin script"
            return
        source_words = {w.text for w in words(ctx.source) if regex.search(r"[A-Za-z]", w.text)}
        lowered = {w.casefold() for w in source_words}
        checked = 0
        for w in words(translation):
            if not regex.fullmatch(r"[A-Za-z0-9\-']+", w.text) or len(w.text) < 3 or not regex.search(r"[A-Za-z]", w.text):
                continue
            checked += 1
            if w.text.casefold() in lowered:
                continue
            best = max(source_words, key=lambda s: similarity(s.casefold(), w.text.casefold()), default=None)
            sim = similarity(best.casefold(), w.text.casefold()) if best else 0.0
            if best and 0.5 <= sim < 1.0:
                ctx.add(
                    Signal(
                        check="latin_terms",
                        category=Category.TERM,
                        score=0.75,
                        severity=Severity.MAJOR,
                        span=(w.start, w.end),
                        message=f"term {w.text!r} in the translation looks like a corrupted {best!r} from the source",
                        data={"source_term": best},
                        hard=True,
                    )
                )
        res.metrics = {"latin_words_checked": checked}
