"""Deterministic translation checks. Free, run on every item, and the first line of evidence.

Each check returns candidate flags with a confidence; nothing here is the final verdict.
None of these checks contain language-specific rules: scripts come from languages.yaml,
digits are normalized through Unicode, and everything else is script-agnostic.
"""

from __future__ import annotations

import re
from collections import Counter

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from bodhan_eval.checks.base import CheckResult, Flag, map_source_span
from bodhan_eval.checks.markup import (
    Block,
    brace_balance_ok,
    env_balance,
    latex_commands,
    mask_regions,
    md_blocks,
    protected_regions,
    stray_dollars,
)
from bodhan_eval.languages import Language
from bodhan_eval.normalize import (
    extract_numbers,
    normalize_text,
    script_of,
    split_sentences,
    to_ascii_digits,
    tokenize,
)
from bodhan_eval.params import Params

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _letter_counts(text: str) -> Counter:
    return Counter(s for ch in text if (s := script_of(ch)))


def _latin_words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[A-Za-z][A-Za-z'-]*", text)]


def _snippet(text: str, span: tuple[int, int], pad: int = 20) -> str:
    s, e = span
    pre = "…" if s - pad > 0 else ""
    post = "…" if e + pad < len(text) else ""
    return f"{pre}{text[max(0, s - pad) : min(len(text), e + pad)]}{post}".replace("\n", " ")


# ---------------------------------------------------------------------------
# 1. Wrong language / script / untranslated
# ---------------------------------------------------------------------------


def check_script(source: str, translation: str, lang: Language, params: Params) -> CheckResult:
    name = "script"
    p = params.section("script")
    if "Latn" in lang.scripts:
        return CheckResult.skipped(name, "target language uses Latin script")

    if normalize_text(source) == normalize_text(translation):
        return CheckResult(
            name,
            flags=[
                Flag(
                    "wrong_language",
                    "critical",
                    name,
                    0.98,
                    (0, len(translation)),
                    "translation is identical to the English source (left untranslated)",
                )
            ],
            signals={"identical_to_source": True},
        )

    regions = protected_regions(translation)
    masked = mask_regions(translation, regions)
    counts = _letter_counts(masked)
    total = sum(counts.values())
    if total < p.get("min_letters", 3):
        return CheckResult.skipped(name, f"only {total} letters outside code/math/URLs")

    accepted = set(lang.scripts)
    share = sum(counts[s] for s in accepted) / total
    dominant = counts.most_common(1)[0][0]
    signals = {
        "target_script_share": round(share, 3),
        "dominant_script": dominant,
        "letters": total,
    }
    res = CheckResult(name, signals=signals)
    src_words = {t.norm for t in tokenize(source)}

    # Judge sentence by sentence first, so a single untranslated sentence is localized
    # instead of condemning the whole text.
    seg_flags: list[Flag] = []
    judged_ok = 0  # letters in qualifying segments that are in the right script
    for s, e in split_sentences(masked):
        seg_counts = _letter_counts(masked[s:e])
        seg_total = sum(seg_counts.values())
        if seg_total < p.get("segment_min_letters", 8):
            continue
        seg_share = sum(seg_counts[x] for x in accepted) / seg_total
        if seg_share > p.get("segment_max_target_share", 0.3):
            judged_ok += seg_total
            continue
        seg_dom = seg_counts.most_common(1)[0][0]
        latin = [w.lower() for w, _, _ in _latin_words(masked[s:e])]
        overlap = sum(w in src_words for w in latin) / len(latin) if latin else 0.0
        untranslated = seg_dom == "Latn" and overlap >= p.get("untranslated_overlap", 0.5)
        seg_flags.append(
            Flag(
                "wrong_language",
                "major",
                name,
                0.85 if untranslated else 0.7,
                (s, e),
                (
                    f"sentence left untranslated in English: “{_snippet(translation, (s, e), 0)}”"
                    if untranslated
                    else f"sentence is in {seg_dom} script, expected {'/'.join(lang.scripts)}"
                ),
                details={"segment_target_share": round(seg_share, 2), "source_overlap": overlap},
            )
        )

    # Whole text wrong: no sentence is in the right script and the overall share is low.
    if judged_ok == 0 and share < p.get("min_target_share", 0.5):
        latin = [w.lower() for w, _, _ in _latin_words(masked)]
        overlap = sum(w in src_words for w in latin) / len(latin) if latin else 0.0
        what = (
            "left untranslated (mostly copied from the source)"
            if dominant == "Latn" and overlap >= p.get("untranslated_overlap", 0.5)
            else f"written in {dominant} script"
        )
        res.flags.append(
            Flag(
                "wrong_language",
                "critical",
                name,
                0.95,
                (0, len(translation)),
                f"only {share:.0%} of letters are in {'/'.join(lang.scripts)} "
                f"(expected for {lang.name}); text is {what}",
                details={"dominant_script": dominant, "source_overlap": round(overlap, 2)},
            )
        )
    else:
        res.flags.extend(seg_flags)
    return res


# ---------------------------------------------------------------------------
# 2. Numbers and units
# ---------------------------------------------------------------------------

_EN_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
    "hundred": "100", "thousand": "1000", "dozen": "12", "first": "1", "second": "2",
    "third": "3", "half": "0.5",
}  # fmt: skip
_SCALE_AFTER = re.compile(r"^\s*(million|billion|trillion|lakh|crore|thousand|hundred)\b", re.I)
_ORDINAL_AFTER = re.compile(r"^(st|nd|rd|th)\b", re.I)

# Language-independent unit symbols and SI/imperial abbreviations (written in Latin script
# in every Indic language when kept as symbols).
_UNIT_SYMBOL = re.compile(r"°\s?[CFK]|°|%|‰|₹|\$|€|£|¥")
_UNIT_WORDS = (
    "km/h kmph mph km cm mm nm µm m kg mg g ml mL L kWh kW MW W mAh V Hz kHz MHz GHz "
    "GB MB KB TB ft lb lbs oz ms min hr hrs"
).split()
_UNIT_ABBR = re.compile(
    r"(?<=\d)\s?("
    + "|".join(re.escape(u) for u in sorted(_UNIT_WORDS, key=len, reverse=True))
    + r")(?![A-Za-z])"
)


def _units(text: str) -> Counter:
    # Math / code / URLs are excluded: "$" there is a delimiter, not a currency.
    t = to_ascii_digits(mask_regions(text, protected_regions(text)))
    c: Counter = Counter()
    for m in _UNIT_SYMBOL.finditer(t):
        c[re.sub(r"\s", "", m.group(0))] += 1
    for m in _UNIT_ABBR.finditer(t):
        c[m.group(1)] += 1
    return c


def check_numbers(source: str, translation: str, lang: Language, params: Params) -> CheckResult:
    name = "numbers"
    # URLs / e-mails / HTML carry digits we do not want to double-count (term check owns them).
    skip = {"url", "email", "html"}
    src = mask_regions(source, [r for r in protected_regions(source) if r.kind in skip])
    tgt = mask_regions(translation, [r for r in protected_regions(translation) if r.kind in skip])
    s_nums = extract_numbers(src)
    t_nums = extract_numbers(tgt)
    src_lower = source.lower()
    word_values = {v for w, v in _EN_NUMBER_WORDS.items() if re.search(rf"\b{w}\b", src_lower)}
    # Digits inside a source word ("Twenty20", "km2") are not counted as source numbers, but
    # a translation may write them apart ("ट्वेंटी 20"): that is not an extra number either.
    word_values |= {
        v for m in re.finditer(r"(?<=[^\W\d_])\d+", to_ascii_digits(src))
        for v in [m.group(0).lstrip("0") or "0"]
    }  # fmt: skip

    def soft(n) -> bool:
        """Source numbers that may legitimately change form (ordinals, scale words)."""
        after = source[n.end : n.end + 12]
        return bool(_ORDINAL_AFTER.match(after) or _SCALE_AFTER.match(after))

    s_count = Counter(n.value for n in s_nums)
    t_count = Counter(n.value for n in t_nums)
    missing_vals = s_count - t_count
    extra_vals = t_count - s_count
    missing = []
    for n in s_nums:
        if missing_vals[n.value] > 0:
            missing.append(n)
            missing_vals[n.value] -= 1
    extra = []
    for n in t_nums:
        if extra_vals[n.value] > 0 and n.value not in word_values:
            extra.append(n)
            extra_vals[n.value] -= 1

    res = CheckResult(
        name,
        signals={"source_numbers": len(s_nums), "missing": len(missing), "extra": len(extra)},
    )
    n_src, n_tgt = max(1, len(source)), max(1, len(translation))

    # Pair each missing source number with the extra target number at the closest relative
    # position: that is a substitution (the classic "100 became 10").
    pairs = []
    free_extra = list(extra)
    for m in missing:
        if not free_extra:
            break
        rel = m.start / n_src
        best = min(free_extra, key=lambda x: abs(x.start / n_tgt - rel))
        pairs.append((m, best))
        free_extra.remove(best)
    paired_missing = {id(m) for m, _ in pairs}

    for m, x in pairs:
        res.flags.append(
            Flag(
                "number",
                "major",
                name,
                0.5 if soft(m) else 0.9,
                (x.start, x.end),
                f"source has {m.raw} (“{_snippet(source, (m.start, m.end))}”) but translation "
                f"has {x.raw} there",
                source_span=(m.start, m.end),
                details={"expected": m.value, "found": x.value},
            )
        )
    for m in missing:
        if id(m) in paired_missing:
            continue
        span, exact = map_source_span(source, translation, (m.start, m.end))
        res.flags.append(
            Flag(
                "number",
                "major",
                name,
                0.25 if soft(m) else (0.6 if exact else 0.5),
                span,
                f"number {m.raw} from the source (“{_snippet(source, (m.start, m.end))}”) "
                "is missing from the translation",
                source_span=(m.start, m.end),
                details={"expected": m.value, "span_exact": exact},
            )
        )
    for x in free_extra:
        res.flags.append(
            Flag(
                "number",
                "major",
                name,
                0.6,
                (x.start, x.end),
                f"translation contains {x.raw}, which does not appear in the source",
                details={"found": x.value},
            )
        )

    # Units: a unit that appears in the translation but not the source is a substitution.
    su, tu = _units(source), _units(translation)
    unit_missing, unit_extra = su - tu, tu - su
    t_ascii = to_ascii_digits(translation)
    for u in unit_extra:
        pos = t_ascii.find(u)
        span = (pos, pos + len(u)) if pos >= 0 else None
        expected = ", ".join(sorted(unit_missing)) or "none"
        res.flags.append(
            Flag(
                "number",
                "major",
                name,
                0.85 if unit_missing else 0.5,
                span,
                f"unit {u} in translation is not in the source (source units missing: {expected})",
                details={"unit_found": u, "units_missing": sorted(unit_missing)},
            )
        )
    if unit_missing and not unit_extra:
        res.signals["units_missing"] = sorted(unit_missing)  # may be spelled out; judge decides
    return res


# ---------------------------------------------------------------------------
# 3. Terms and entities
# ---------------------------------------------------------------------------


_EN_FUNCTION_WORDS = set(
    "the a an this that these those in on at of for from by with to and or but if when while "
    "as it its our their his her my your we they he she i you".split()
)


def source_entities(source: str) -> list[tuple[str, tuple[int, int]]]:
    """Likely names / technical terms in the English source: acronyms, capitalised words
    that do not start a sentence, and alphanumeric identifiers. Used by back-translation."""
    out = []
    sent_starts = {s for s, _ in split_sentences(source)}
    masked = mask_regions(source, protected_regions(source))
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*)*", masked):
        word, (s, e) = m.group(0), m.span()
        first, _, rest = word.partition(" ")
        if first.lower() in _EN_FUNCTION_WORDS:  # "The Indian Space ..." -> "Indian Space ..."
            if not rest:
                continue
            word, s = rest.lstrip(), e - len(rest.lstrip())
        elif s in sent_starts and " " not in word and not word.isupper():
            continue  # ordinary sentence-initial word
        out.append((word, (s, e)))
    for m in re.finditer(r"\b[a-z]+[0-9]+[A-Za-z0-9]*\b", masked):
        out.append((m.group(0), m.span()))
    return out


def _best_window(needle: str, hay: str) -> tuple[tuple[int, int], float]:
    al = fuzz.partial_ratio_alignment(needle, hay)
    if al is None:
        return (0, 0), 0.0
    return (al.dest_start, al.dest_end), al.score


def check_terms(source: str, translation: str, lang: Language, params: Params) -> CheckResult:
    name = "terms"
    res = CheckResult(name)
    src_regions = protected_regions(source)
    tgt_regions = protected_regions(translation)
    tgt_code = " ".join(r.text for r in tgt_regions if r.kind == "code_block")

    # Verbatim content: URLs, e-mails and inline code must be copied exactly.
    checked = 0
    for r in src_regions:
        if r.kind in ("url", "email", "inline_code"):
            checked += 1
            if r.text in translation:
                continue
            in_link = source[max(0, r.start - 2) : r.start] == "]("
            span, score = _best_window(r.text, translation)
            if score < 60:
                span, _ = map_source_span(source, translation, (r.start, r.end))
            res.flags.append(
                Flag(
                    "format" if in_link else "term",
                    "major",
                    name,
                    0.85 if score >= 60 else 0.7,
                    span,
                    f"{r.kind.replace('_', ' ')} “{r.text}” is not preserved verbatim"
                    + (f"; closest is “{translation[span[0] : span[1]]}”" if score >= 60 else ""),
                    source_span=(r.start, r.end),
                    details={"kind": r.kind, "similarity": round(score, 1)},
                )
            )
        elif r.kind == "code_block":
            # Comments may be translated; identifiers must survive.
            idents = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", r.text)) - {"```"}
            lost = sorted(i for i in idents if i not in tgt_code)
            checked += 1
            if lost and len(lost) / max(1, len(idents)) > 0.2:
                span, _ = map_source_span(source, translation, (r.start, r.end))
                res.flags.append(
                    Flag(
                        "format",
                        "major",
                        name,
                        0.75,
                        span,
                        f"code block identifiers changed or missing: {', '.join(lost[:6])}",
                        source_span=(r.start, r.end),
                    )
                )

    # Latin-script words that the translation introduced but the source never had: an
    # invented / swapped name or term (e.g. source "Paris", translation "London").
    if "Latn" not in lang.scripts:
        min_len = params.get("term", "min_foreign_latin_len", 3)
        src_words = {w.lower() for w, _, _ in _latin_words(source)}
        masked = mask_regions(translation, tgt_regions)
        for w, s, e in _latin_words(masked):
            lw = w.lower()
            if len(lw) < min_len or lw in src_words:
                continue
            if any(Levenshtein.normalized_similarity(lw, sw) >= 0.75 for sw in src_words):
                continue  # inflection / spelling variant of a source word
            if w in _UNIT_WORDS:
                continue  # unit abbreviations are the number check's job
            res.flags.append(
                Flag(
                    "term",
                    "major",
                    name,
                    0.55,
                    (s, e),
                    f"Latin-script word “{w}” in the translation does not occur in the source",
                    details={"word": w},
                )
            )
    res.signals = {"verbatim_checked": checked}
    return res


# ---------------------------------------------------------------------------
# 4. Markdown / LaTeX format
# ---------------------------------------------------------------------------

_LINK = re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)")


def _inline_checks(
    src: str, tgt: str, s_off: int, t_off: int, t_block_span: tuple[int, int], name: str
) -> list[Flag]:
    flags: list[Flag] = []
    s_regions, t_regions = protected_regions(src), protected_regions(tgt)

    def fl(sev, conf, ev, details=None):
        flags.append(Flag("format", sev, name, conf, t_block_span, ev, details=details or {}))

    n_s_code = sum(r.kind == "inline_code" for r in s_regions)
    n_t_code = sum(r.kind == "inline_code" for r in t_regions)
    if n_s_code != n_t_code and tgt.count("`") % 2 == 1:
        fl("major", 0.85, "unbalanced backtick: inline code is broken")
    if len(_LINK.findall(src)) != len(_LINK.findall(tgt)):
        fl(
            "major",
            0.8,
            f"link count differs: source {len(_LINK.findall(src))}, "
            f"translation {len(_LINK.findall(tgt))}",
        )
    if src.count("**") % 2 == 0 and tgt.count("**") % 2 == 1:
        fl("minor", 0.75, "unbalanced ** (bold) markers")

    s_math = [r for r in s_regions if r.kind.startswith("math")]
    t_math = [r for r in t_regions if r.kind.startswith("math")]
    stray = stray_dollars(tgt, t_regions)
    if len(s_math) != len(t_math):
        if stray and not stray_dollars(src, s_regions):
            p = stray[0] + t_off
            fl(
                "major",
                0.9,
                f"unbalanced $ delimiter: source has {len(s_math)} math "
                f"expression(s), translation {len(t_math)}",
                {"stray_dollar_at": p},
            )
        else:
            fl(
                "major",
                0.7,
                f"math expression count differs: source {len(s_math)}, translation {len(t_math)}",
            )
    for sm, tm in zip(s_math, t_math, strict=False):
        tspan = (tm.start + t_off, tm.end + t_off)
        sc, tc = latex_commands(sm.text), latex_commands(tm.text)
        if not brace_balance_ok(tm.text) and brace_balance_ok(sm.text):
            flags.append(
                Flag(
                    "format",
                    "major",
                    name,
                    0.9,
                    tspan,
                    f"unbalanced braces in LaTeX “{tm.text[:60]}”",
                    source_span=(sm.start + s_off, sm.end + s_off),
                )
            )
        elif sc != tc:
            lost = sorted((Counter(sc) - Counter(tc)).elements())
            added = sorted((Counter(tc) - Counter(sc)).elements())
            flags.append(
                Flag(
                    "format",
                    "major",
                    name,
                    0.85,
                    tspan,
                    f"LaTeX commands differ (missing: {lost or '-'}, added: "
                    f"{added or '-'}) in “{tm.text[:60]}”",
                    source_span=(sm.start + s_off, sm.end + s_off),
                )
            )
    if env_balance(tgt) and not env_balance(src):
        fl("major", 0.85, f"unbalanced LaTeX environments: {env_balance(tgt)}")
    return flags


def check_format(source: str, translation: str, params: Params) -> CheckResult:
    name = "format"
    sb, tb = md_blocks(source), md_blocks(translation)
    res = CheckResult(
        name,
        signals={
            "source_blocks": len(sb),
            "translation_blocks": len(tb),
            "structural": sum(b.structural for b in sb),
        },
    )
    if not sb or not tb:
        return res

    def sp(b: Block) -> tuple[int, int]:
        return (b.start, b.end)

    def label(b: Block) -> str:
        return {"ul": "list item", "ol": "numbered item", "table_row": "table row",
                "table_sep": "table header separator", "fence": "code fence", "math": "math block",
                "hr": "horizontal rule", "quote": "quote", "para": "paragraph"}.get(
            b.kind, f"level-{b.kind[1:]} heading")  # fmt: skip

    pairs: list[tuple[Block, Block]] = []
    for op in Levenshtein.opcodes([b.kind for b in sb], [b.kind for b in tb]):
        s_part, t_part = sb[op.src_start : op.src_end], tb[op.dest_start : op.dest_end]
        if op.tag == "equal":
            pairs.extend(zip(s_part, t_part, strict=False))
            continue
        if op.tag == "replace":
            for s, t in zip(s_part, t_part, strict=False):
                pairs.append((s, t))
                if s.structural or t.structural:
                    res.flags.append(
                        Flag(
                            "format",
                            "major",
                            name,
                            0.85,
                            sp(t),
                            f"source {label(s)} became a {label(t)} in the translation",
                            source_span=sp(s),
                            details={"from": s.kind, "to": t.kind},
                        )
                    )
            s_part, t_part = s_part[len(t_part) :], t_part[len(s_part) :]
        for s in s_part:  # block missing entirely
            near = tb[min(op.dest_start, len(tb) - 1)]
            marker_only = s.kind in ("table_sep", "fence", "hr", "math")
            res.flags.append(
                Flag(
                    "format" if marker_only else "omission",
                    "major",
                    name,
                    0.85 if marker_only else 0.6,
                    sp(near),
                    f"source {label(s)} “{source[s.start : s.end][:60]}” has no counterpart in "
                    "the translation",
                    source_span=sp(s),
                    details={"missing_block": s.kind},
                )
            )
        for t in t_part:  # block that the source does not have
            marker_only = t.kind in ("table_sep", "fence", "hr", "math")
            res.flags.append(
                Flag(
                    "format" if marker_only else "addition",
                    "major",
                    name,
                    0.85 if marker_only else 0.5,
                    sp(t),
                    f"translation has an extra {label(t)} not present in the source",
                    details={"extra_block": t.kind},
                )
            )

    for s, t in pairs:
        if s.cells is not None and t.cells is not None and s.cells != t.cells:
            res.flags.append(
                Flag(
                    "format",
                    "major",
                    name,
                    0.9,
                    sp(t),
                    f"{label(t)} has {t.cells} cells, source row has {s.cells}",
                    source_span=sp(s),
                )
            )
        res.flags.extend(
            _inline_checks(
                source[s.start : s.end], translation[t.start : t.end], s.start, t.start, sp(t), name
            )
        )
    return res


# ---------------------------------------------------------------------------
# 5. Length signals (no flags on their own; used for routing and to back omission/addition)
# ---------------------------------------------------------------------------


def length_signals(source: str, translation: str) -> dict:
    def content_len(t: str) -> int:
        return sum(1 for ch in t if ch.isalnum() or script_of(ch))

    s, t = content_len(source), content_len(translation)
    return {
        "char_ratio": round(t / s, 4) if s else None,
        "source_sentences": len(split_sentences(source)),
        "translation_sentences": len(split_sentences(translation)),
    }


def run_translation_checks(
    source: str, translation: str, lang: Language, params: Params
) -> list[CheckResult]:
    results = [
        check_script(source, translation, lang, params),
        check_numbers(source, translation, lang, params),
        check_terms(source, translation, lang, params),
    ]
    if params.get("format", "enabled", True):
        results.append(check_format(source, translation, params))
    results.append(CheckResult("length", signals=length_signals(source, translation)))
    return results
