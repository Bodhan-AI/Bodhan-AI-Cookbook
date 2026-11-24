"""Tier-0 translation checks: local, deterministic, free.

* ``script``      wrong language / wrong script / text left in English
* ``format``      Markdown, tables, LaTeX, code and links survive translation
* ``length``      length ratio outliers vs this language's own distribution (weak omission/addition hint)
* ``reference``   chrF++ / BLEU when a reference exists (metrics only; never flags on its own)
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from .. import markup
from ..config import lang_info, langs, same_script_languages
from ..models import ItemResult
from ..normalize import STOPWORDS, normalize_text, segments, word_tokens
from .base import Check, SkipCheck

# ---------------------------------------------------------------------------
# script / language
# ---------------------------------------------------------------------------


def _script_table() -> list[tuple[int, int, str]]:
    table = []
    for name, ranges in langs()["scripts"].items():
        for lo, hi in ranges:
            table.append((lo, hi, name))
    return table


_SCRIPTS = _script_table()


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for lo, hi, name in _SCRIPTS:
        if lo <= cp <= hi:
            return name
    return None


def letter_scripts(text: str, skip: list[tuple[int, int]] | None = None) -> Counter:
    """Count letters (and combining marks) by script, ignoring protected regions."""
    import unicodedata

    counts: Counter = Counter()
    for i, ch in enumerate(text):
        if skip and markup.in_spans(i, skip):
            continue
        if unicodedata.category(ch)[0] not in "LM":
            continue
        counts[script_of(ch) or "OTHER"] += 1
    return counts


def source_latin_words(source: str) -> set[str]:
    """Latin words that may legitimately stay in Latin script: names, acronyms and formula-like terms only.
    (Allowing every source word would hide a fully untranslated output.)"""
    from .roundtrip import source_entities

    words: set[str] = set()
    for ent, _, _ in source_entities(source, limit=50):
        words.update(w.lower() for w in re.findall(r"[A-Za-z0-9]+", ent))
    return words


def _allowed_latin_spans(target: str, source_words: set[str]) -> list[tuple[int, int]]:
    """Latin words that legitimately stay in Latin: names/terms copied from the source, and protected markup."""
    spans = markup.protected_spans(target)
    for t in word_tokens(target):
        if t.text.isascii() and t.text.lower() in source_words and t.text.lower() not in STOPWORDS:
            spans.append((t.start, t.end))
    return spans


def _is_gloss(text: str, span: tuple[int, int], script: str | None) -> bool:
    """English in brackets right after a word of the target script: the original term given next to its
    translation, e.g. ``நியூட்டனின் இயக்க விதிகள் (Newton's laws of motion)``, not text left untranslated."""
    open_, close = text.rfind("(", 0, span[0]), text.find(")", span[1])
    if not script or open_ < 0 or close < 0 or ")" in text[open_ : span[0]] or not text[open_ + 1 : close].isascii():
        return False
    before = text[:open_].rstrip()
    return bool(before) and unicodedata.category(before[-1])[0] in "LM" and unicodedata.name(before[-1], "").startswith(script)


class ScriptCheck(Check):
    name = "script"
    tier = 0
    description = "Unicode-script share vs the language's script; English left untranslated; same-script language confusion by function words."

    def applies(self, r, ctx):
        return bool(r.item.translation and r.item.translation.strip())

    def run(self, r: ItemResult, ctx) -> None:
        info = lang_info(r.item.lang)
        if not info.script:
            raise SkipCheck(f"no script data for '{r.item.lang}' (add it to data/langs.json)")
        tgt, src = self.translation(r), r.item.source
        src_words = source_latin_words(src)
        allowed = _allowed_latin_spans(tgt, src_words)
        counts = letter_scripts(tgt, allowed)
        total = sum(counts.values())
        share = counts[info.script] / total if total else 1.0
        r.metrics["script_share"] = round(share, 4)
        min_share = self.param(ctx, "script_min_expected_share")
        if total >= 5 and share < min_share:
            dominant, _ = counts.most_common(1)[0]
            what = "left in English (untranslated)" if dominant == "LATIN" else f"written in {dominant.title()} script"
            self.signal(
                r,
                "wrong_language",
                0.95 if share < 0.25 else 0.8,
                f"Only {share:.0%} of letters are {info.script.title()} (expected for {info.name}); text is mostly {what}.",
                severity="critical",
                span=(0, len(tgt)),
                details={"script_counts": dict(counts)},
            )
            return
        self._segments(r, ctx, info, tgt, src_words)
        self._same_script(r, info, tgt)

    def _segments(self, r, ctx, info, tgt, src_words):
        min_words = self.param(ctx, "untranslated_min_words")
        allowed = _allowed_latin_spans(tgt, src_words)
        for a, b in segments(tgt):
            seg = tgt[a:b]
            counts = letter_scripts(seg, [(x - a, y - a) for x, y in allowed])
            total = sum(counts.values())
            if total < 12:
                continue
            share = counts[info.script] / total
            if share >= 0.3:
                continue
            dominant, _ = counts.most_common(1)[0]
            if dominant == "LATIN":
                words = [t.text.lower() for t in word_tokens(seg) if t.text.isascii() and t.text.isalpha()]
                stop_hits = sum(w in STOPWORDS for w in words)
                if len(words) >= min_words and stop_hits >= 1:
                    self.signal(
                        r,
                        "wrong_language",
                        0.85,
                        f"Segment left in English: {seg[:80]!r}",
                        severity="major",
                        span=(a, b),
                    )
            else:
                self.signal(
                    r,
                    "wrong_language",
                    0.9,
                    f"Segment written in {dominant.title()} script, expected {info.script.title()}: {seg[:60]!r}",
                    severity="major",
                    span=(a, b),
                )
        # English runs inside otherwise-translated sentences. A number or a kept name inside a run neither ends it
        # nor counts as English: "The Ganga is 2,525 km long." left untranslated is one run of 4 English words.
        toks = word_tokens(tgt)
        run: list = []
        for t in toks + [None]:
            is_eng = t is not None and t.text.isascii() and t.text.isalpha() and not markup.in_spans(t.start, allowed)
            if is_eng:
                run.append(t)
                continue
            neutral = t is not None and (
                any(c.isdigit() for c in t.text) or (t.text.isascii() and markup.in_spans(t.start, allowed))
            )
            if neutral:
                continue
            if len(run) >= min_words and sum(x.text.lower() in STOPWORDS for x in run) >= 2:
                span = (run[0].start, run[-1].end)
                if _is_gloss(tgt, span, info.script):
                    run = []
                    continue
                if not any(
                    s.check == self.name and s.span and s.span[0] <= span[0] and span[1] <= s.span[1] for s in r.signals
                ):
                    self.signal(
                        r,
                        "wrong_language",
                        0.7,
                        f"{len(run)} consecutive English words left untranslated: {tgt[span[0] : span[1]][:80]!r}",
                        severity="major",
                        span=span,
                    )
            run = []

    def _same_script(self, r, info, tgt):
        if not (info.function_words or info.marker_chars):
            return
        words = Counter(t.text for t in word_tokens(normalize_text(tgt, casefold=False, punct=False)))

        def score(code: str) -> float:
            li = lang_info(code)
            fw = sum(words[w] for w in li.function_words)
            mk = sum(tgt.count(c) for c in li.marker_chars)
            return fw + min(mk, 10) * 0.5

        own = score(info.code)
        rivals = [
            (score(c), c)
            for c in same_script_languages(info.code)
            if lang_info(c).function_words or lang_info(c).marker_chars
        ]
        if not rivals:
            return
        best, code = max(rivals)
        r.metrics["language_profile"] = {"own": own, "best_rival": code, "rival": best}
        if best >= 3 and best >= 2 * own + 1:
            rival = lang_info(code)
            self.signal(
                r,
                "wrong_language",
                min(0.9, 0.55 + 0.05 * (best - own)),
                f"Text reads as {rival.name}, not {info.name}: {rival.name} markers score {best:g} vs {own:g} for {info.name}.",
                severity="critical",
                span=(0, len(tgt)),
            )


# ---------------------------------------------------------------------------
# format
# ---------------------------------------------------------------------------


class FormatCheck(Check):
    name = "format"
    tier = 0
    description = (
        "Markdown headings/lists/tables, code, links and LaTeX must survive translation; delimiters must balance."
    )

    def applies(self, r, ctx):
        return bool(r.item.translation) and (
            markup.has_markup(r.item.source) or markup.has_markup(r.item.translation or "")
        )

    def run(self, r: ItemResult, ctx) -> None:
        src, tgt = r.item.source, self.translation(r)
        self._lines(r, src, tgt)
        self._math(r, src, tgt)
        self._inline(r, src, tgt)

    def _lines(self, r, src, tgt):
        sl = [ln for ln in markup.md_lines(src) if ln.kind != "blank"]
        tl = [ln for ln in markup.md_lines(tgt) if ln.kind != "blank"]
        if len(sl) == len(tl):
            for s, t in zip(sl, tl):
                if s.kind == t.kind and s.cols == t.cols:
                    continue
                if s.kind == "text" and t.kind == "text":
                    continue
                what = f"{s.kind}{f' ({s.cols} cols)' if s.cols else ''} became {t.kind}{f' ({t.cols} cols)' if t.cols else ''}"
                sev = "major" if s.kind.startswith(("table", "heading", "fence")) else "minor"
                self.signal(
                    r,
                    "format",
                    0.9,
                    f"Markdown line structure changed: {what}: {tgt[t.start : t.end][:80]!r}",
                    severity=sev,
                    span=(t.start, t.end),
                    source_span=(s.start, s.end),
                )
            return
        # line counts differ: compare structural counts, and check every target table against its own header
        sc, tc = Counter(ln.kind for ln in sl), Counter(ln.kind for ln in tl)
        for kind in sorted(set(sc) | set(tc)):
            if kind == "text" or sc[kind] == tc[kind]:
                continue
            self.signal(
                r,
                "format",
                0.6,
                f"Markdown {kind} count changed from {sc[kind]} to {tc[kind]}",
                severity="major",
                span=None,
            )
        self._tables_self_consistent(r, tgt)

    def _tables_self_consistent(self, r, tgt):
        lines = markup.md_lines(tgt)
        header = None
        for ln in lines:
            if ln.kind in ("table", "table_sep"):
                if header is None:
                    header = ln.cols
                elif ln.cols != header:
                    self.signal(
                        r,
                        "format",
                        0.85,
                        f"Table row has {ln.cols} columns, header has {header}: {tgt[ln.start : ln.end][:80]!r}",
                        severity="major",
                        span=(ln.start, ln.end),
                    )
            else:
                header = None

    def _math(self, r, src, tgt):
        s_math, t_math = markup.math_segments(src), markup.math_segments(tgt)
        stray = markup.unbalanced_dollars(tgt) if not markup.unbalanced_dollars(src) else []
        unmatched_t = list(t_math)
        for s in s_math:
            hit = next((t for t in unmatched_t if t.key == s.key), None)
            if hit:
                unmatched_t.remove(hit)
                continue
            # closest remaining target formula
            from ..spans import similarity

            best = max(unmatched_t, key=lambda t: similarity(t.key, s.key), default=None)
            if best is not None and similarity(best.key, s.key) >= 0.5:
                unmatched_t.remove(best)
                if re.sub(r"\d", "#", best.key) == re.sub(r"\d", "#", s.key):
                    continue  # only digits differ: the number check owns this
                self.signal(
                    r,
                    "format",
                    0.85,
                    f"LaTeX changed: {src[s.start : s.end][:60]!r} -> {tgt[best.start : best.end][:60]!r}",
                    severity="major",
                    span=(best.start, best.end),
                    source_span=(s.start, s.end),
                )
            elif not stray:  # a stray "$" means the formula is present but broken; reported below
                self.signal(
                    r,
                    "format",
                    0.6,
                    f"LaTeX formula missing from translation: {src[s.start : s.end][:60]!r}",
                    severity="major",
                    span=None,
                    source_span=(s.start, s.end),
                )
        for pos in stray:
            end = tgt.find("\n", pos)
            end = len(tgt) if end < 0 else end
            self.signal(
                r,
                "format",
                0.9,
                f"Unmatched '$' math delimiter: {tgt[pos:end][:60]!r}",
                severity="major",
                span=(pos, end),
            )
        for t in t_math:
            if markup.brace_problems(t.body) and not any(markup.brace_problems(s.body) for s in s_math):
                self.signal(
                    r,
                    "format",
                    0.85,
                    f"Unbalanced braces in LaTeX: {tgt[t.start : t.end][:60]!r}",
                    severity="major",
                    span=(t.start, t.end),
                )

    def _inline(self, r, src, tgt):
        for marker, label in (("**", "bold marker '**'"), ("`", "backtick")):
            if not markup.unmatched_marker_positions(src, marker):
                for pos in markup.unmatched_marker_positions(tgt, marker):
                    self.signal(
                        r,
                        "format",
                        0.8,
                        f"Unmatched {label} in translation",
                        severity="minor",
                        span=(pos, pos + len(marker)),
                    )
        s_code = Counter(m.group(0) for m in markup.INLINE_CODE_RE.finditer(src))
        t_code = Counter(m.group(0) for m in markup.INLINE_CODE_RE.finditer(tgt))
        for code in s_code - t_code:
            self.signal(r, "format", 0.6, f"Inline code {code!r} changed or missing", severity="minor", span=None)
        s_urls = Counter(m.group(2) for m in markup.LINK_RE.finditer(src))
        t_urls = Counter(m.group(2) for m in markup.LINK_RE.finditer(tgt))
        for url in s_urls - t_urls:
            self.signal(r, "format", 0.75, f"Link target {url!r} missing or altered", severity="minor", span=None)


# ---------------------------------------------------------------------------
# length ratio
# ---------------------------------------------------------------------------


def length_log_ratio(source: str, translation: str) -> float | None:
    s = len(re.sub(r"\s+", "", source))
    t = len(re.sub(r"\s+", "", translation))
    if s < 20 or t == 0:
        return None
    return math.log(t / s)


class LengthCheck(Check):
    name = "length"
    tier = 0
    description = "Length ratio vs the language's own median in this dataset (robust z-score). A weak hint only; never flags alone."

    def applies(self, r, ctx):
        return bool(r.item.translation)

    def run(self, r: ItemResult, ctx) -> None:
        lr = length_log_ratio(r.item.source, self.translation(r))
        if lr is None:
            raise SkipCheck("source too short for a length ratio")
        med, sd, n = ctx.stats.length_ratio.get(r.item.lang, (math.log(langs()["priors"]["length_ratio"]), 0.35, 0))
        z = (lr - med) / max(sd, 0.08)
        r.metrics["length_z"] = round(z, 3)
        thr = self.param(ctx, "length_ratio_z")
        if abs(z) < thr:
            return
        pct = math.exp(lr - med)
        cat = "omission" if z < 0 else "addition"
        self.signal(
            r,
            cat,
            min(0.35, 0.15 + 0.04 * (abs(z) - thr)),
            f"Translation is {pct:.0%} of the typical {r.item.lang} length for its source (z={z:+.1f}, n={n}).",
            span=None,
            details={"z": round(z, 3)},
        )


# ---------------------------------------------------------------------------
# reference-based metrics
# ---------------------------------------------------------------------------


class ReferenceCheck(Check):
    name = "reference"
    tier = 0
    description = (
        "chrF++ and BLEU against the reference translation when one is supplied (reported, never flags on its own)."
    )

    def applies(self, r, ctx):
        return bool(r.item.translation and r.item.reference)

    def run(self, r: ItemResult, ctx) -> None:
        from sacrebleu.metrics import BLEU, CHRF

        if r.item.reference is None:
            raise SkipCheck("no reference translation")
        hyp = normalize_text(self.translation(r), casefold=False)
        ref = normalize_text(r.item.reference, casefold=False)
        r.metrics["chrf_pp"] = round(CHRF(word_order=2).sentence_score(hyp, [ref]).score, 2)
        r.metrics["bleu"] = round(BLEU(effective_order=True).sentence_score(hyp, [ref]).score, 2)
