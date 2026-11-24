"""Wrong language or script, and text left untranslated."""

from __future__ import annotations

import unicodedata
from collections import Counter

import regex

from ..config import Thresholds
from ..languages import Language, same_script_siblings, script_of
from ..models import Category, Severity, Signal
from ..text.markup import INLINE_CODE, LINK, math_segments
from ..text.normalize import norm_text
from ..text.segment import sentences, words
from .base import ItemContext

URL = regex.compile(r"https?://\S+|www\.\S+")


def _masked(text: str) -> str:
    chars = list(text)
    spans = [(m.start(), m.end()) for m in math_segments(text)]
    spans += [(m.start(), m.end()) for rx in (INLINE_CODE, URL) for m in rx.finditer(text)]
    spans += [(m.start(1), m.end(1)) for m in LINK.finditer(text)]
    for s, e in spans:
        for k in range(s, e):
            chars[k] = " "
    return "".join(chars)


def _word_script(word: str) -> str | None:
    counts = Counter(script_of(ch) for ch in word if unicodedata.category(ch)[0] in "LM")
    counts.pop(None, None)
    return counts.most_common(1)[0][0] if counts else None


def check_script(ctx: ItemContext, th: Thresholds) -> None:
    text = ctx.translation or ""
    lang = ctx.lang
    with ctx.check("script_language", "deterministic") as res:
        masked = _masked(text)
        ws = words(masked)
        scripts = [(w, _word_script(w.text)) for w in ws]
        source_words = {w.text.casefold() for w in words(ctx.source)}
        letters: Counter = Counter()
        copied_latin = 0
        for w, sc in scripts:
            if not sc:
                continue
            n = sum(1 for ch in w.text if unicodedata.category(ch)[0] in "LM")
            if sc == "Latn" and lang.script != "Latn" and w.text.casefold() in source_words:
                copied_latin += n  # names and terms kept from the source are legitimate in any script
            else:
                letters[sc] += n
        total = sum(letters.values())
        res.metrics = {"letters_by_script": dict(sorted(letters.items())), "copied_latin_letters": copied_latin, "expected_script": lang.script or None}
        if total == 0 and copied_latin:
            ctx.add(
                Signal(
                    check="script_language",
                    category=Category.WRONG_LANGUAGE,
                    score=0.95,
                    severity=Severity.CRITICAL,
                    span=(0, len(text)),
                    message="translation is the English source left untranslated",
                    hard=True,
                )
            )
            return
        if total == 0:
            if ctx.source.strip() and words(ctx.source):
                ctx.add(
                    Signal(
                        check="script_language",
                        category=Category.OMISSION,
                        score=0.9,
                        severity=Severity.CRITICAL,
                        span=(0, len(text)),
                        source_span=(0, len(ctx.source)),
                        message="translation has no letters at all",
                        hard=True,
                    )
                )
            return
        _untranslated_runs(ctx, th, scripts, source_words)
        if not lang.script:
            res.reason = f"language {lang.code!r} is not in the registry; script ratio checks skipped"
            return
        expected_ratio = letters[lang.script] / total
        res.metrics["expected_script_ratio"] = round(expected_ratio, 4)
        if lang.script != "Latn" and expected_ratio < th.expected_script_min_ratio:
            dominant = letters.most_common(1)[0][0]
            ctx.add(
                Signal(
                    check="script_language",
                    category=Category.WRONG_LANGUAGE,
                    score=0.95,
                    severity=Severity.CRITICAL,
                    span=(0, len(text)),
                    message=f"only {expected_ratio:.0%} of letters are in {lang.script}; dominant script is {dominant}",
                    hard=True,
                    data={"expected_ratio": expected_ratio, "dominant": dominant},
                )
            )
            return
        _foreign_runs(ctx, th, scripts, lang, total)
        _sibling_language(ctx, lang, text)


def _untranslated_runs(ctx: ItemContext, th: Thresholds, scripts, source_words: set[str]) -> None:
    if ctx.lang.script == "Latn":
        return
    run: list = []
    runs = []
    for w, sc in scripts + [(None, None)]:
        if sc == "Latn":
            run.append(w)
        else:
            if run:
                runs.append(run)
            run = []
    for run in runs:
        in_source = sum(1 for w in run if w.text.casefold() in source_words)
        if len(run) >= th.latin_run_words and in_source / len(run) >= 0.6:
            span = (run[0].start, run[-1].end)
            ctx.add(
                Signal(
                    check="script_language",
                    category=Category.WRONG_LANGUAGE,
                    score=0.85,
                    severity=Severity.MAJOR,
                    span=span,
                    message=f"{len(run)} consecutive source words left untranslated in English",
                    hard=True,
                    data={"words": len(run)},
                )
            )


def _foreign_runs(ctx: ItemContext, th: Thresholds, scripts, lang: Language, total: int) -> None:
    foreign = [(w, sc) for w, sc in scripts if sc and sc not in (lang.script, "Latn")]
    if not foreign:
        return
    foreign_letters = sum(len(w.text) for w, _ in foreign)
    ratio = foreign_letters / total
    spans: list[list[int]] = []
    for w, _ in foreign:
        if spans and w.start - spans[-1][1] <= 2:
            spans[-1][1] = w.end
        else:
            spans.append([w.start, w.end])
    other = Counter(sc for _, sc in foreign).most_common(1)[0][0]
    score = 0.9 if ratio >= th.foreign_script_ratio else 0.55
    for s, e in spans:
        ctx.add(
            Signal(
                check="script_language",
                category=Category.WRONG_LANGUAGE,
                score=score,
                severity=Severity.MAJOR if ratio >= th.foreign_script_ratio else Severity.MINOR,
                span=(s, e),
                message=f"text in {other} script inside a {lang.script} translation ({ratio:.0%} of letters)",
                hard=ratio >= th.foreign_script_ratio,
                data={"foreign_ratio": round(ratio, 4), "script": other},
            )
        )


def _marker_hits(text: str, markers: tuple[str, ...]) -> int:
    toks = Counter(w.text for w in words(norm_text(text)))
    return sum(toks[m] for m in markers)


def _sibling_language(ctx: ItemContext, lang: Language, text: str) -> None:
    letters = sum(1 for ch in text if unicodedata.category(ch).startswith("L"))
    forbidden = sum(text.count(ch) for ch in lang.forbidden_chars)
    if forbidden >= 2:
        ctx.add(
            Signal(
                check="script_language",
                category=Category.WRONG_LANGUAGE,
                score=0.8,
                severity=Severity.CRITICAL,
                span=(0, len(text)),
                message=f"characters {list(lang.forbidden_chars)} do not occur in {lang.name}; text looks like a sibling language",
                data={"count": forbidden},
            )
        )
        return
    if lang.required_chars and letters >= 80 and not any(ch in text for ch in lang.required_chars):
        ctx.add(
            Signal(
                check="script_language",
                category=Category.WRONG_LANGUAGE,
                score=0.6,
                severity=Severity.CRITICAL,
                span=(0, len(text)),
                message=f"no {list(lang.required_chars)} in {letters} letters; {lang.name} text normally has them",
            )
        )
        return
    if not lang.markers:
        return
    siblings = [s for s in same_script_siblings(lang.code) if s.markers]
    if not siblings:
        return
    own_total = _marker_hits(text, lang.markers)
    for sib in siblings:
        sib_total = _marker_hits(text, sib.markers)
        if sib_total >= 3 and sib_total > 2 * own_total:
            ctx.add(
                Signal(
                    check="script_language",
                    category=Category.WRONG_LANGUAGE,
                    score=0.75,
                    severity=Severity.CRITICAL,
                    span=(0, len(text)),
                    message=f"{sib_total} {sib.name} function words vs {own_total} {lang.name} ones",
                    data={"sibling": sib.code},
                )
            )
            return
    for sent in sentences(text):
        if len(words(sent.text)) < 5:
            continue
        own = _marker_hits(sent.text, lang.markers)
        for sib in siblings:
            hits = _marker_hits(sent.text, sib.markers)
            if hits >= 2 and own == 0:
                ctx.add(
                    Signal(
                        check="script_language",
                        category=Category.WRONG_LANGUAGE,
                        score=0.6,
                        severity=Severity.MAJOR,
                        span=(sent.start, sent.end),
                        message=f"sentence reads as {sib.name} ({hits} function words), not {lang.name}",
                        data={"sibling": sib.code},
                    )
                )
                break
