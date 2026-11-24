"""Deterministic translation checks (plan §9). Free, rule-based, run on every item.

Each check returns candidates, not flags: fusion (§13) decides what is reported. `standalone`
marks the checks precise enough to flag on their own (format, numbers missing from both the
translation and its back-translation, wrong script).

Spans are offsets into the original strings: `side` says which one ("translation" or "source").
"""

from __future__ import annotations

import difflib
import math
import re
from decimal import Decimal
from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from sacrebleu.metrics import BLEU, CHRF

from .langs import script_counts
from .normalize import normalize
from .segment import _MATH, segments


@dataclass
class Candidate:
    check: str
    category: str  # omission, addition, mistranslation, number, term, format, wrong_language
    side: str  # "translation" or "source"
    span: tuple[int, int]
    score: float  # 0-1 strength of the evidence
    evidence: dict = field(default_factory=dict)
    severity: str | None = None  # minor, major, critical: a hint for fusion
    standalone: bool = False


# Regions whose content is not translated prose: excluded from number, script and entity checks.
_CODE_BLOCK = re.compile(r"^ {0,3}```.*?(?:^ {0,3}```[^\n]*$|\Z)", re.S | re.M)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_URL = re.compile(r"https?://[^\s)>\]]+")


def masked(text: str) -> list[tuple[int, int]]:
    return sorted(m.span() for p in (_CODE_BLOCK, _INLINE_CODE, _MATH, _URL) for m in p.finditer(text))


def _inside(span: tuple[int, int], regions: list[tuple[int, int]]) -> bool:
    return any(s <= span[0] and span[1] <= e for s, e in regions)


# --- 1. Wrong language, wrong script, untranslated ---------------------------------------------

_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z'’-]*(?:[\s,]+[A-Za-z][A-Za-z'’-]*)*")


def check_script(source: str, translation: str, expected: set[str] | None) -> list[Candidate]:
    """Per sentence: mostly the wrong script -> wrong_language; runs of 2+ Latin words -> untranslated.

    Latin is excused when it is a name or term from the source (kept in Latin on purpose) or sits in
    code, math or a URL. Ordinary source words copied over are not excused: that is untranslated text.
    """
    if not expected or "LATIN" in expected:
        return []
    source_words = {w.casefold() for text, _ in entities(source) for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", text)}
    skip = masked(translation)
    out, considered = [], 0
    for seg in segments(translation):
        if _inside((seg.start, seg.end), skip):
            continue
        counts = script_counts(seg.text)
        unexcused = []  # Latin runs (2+ words) that are not names/terms copied from the source
        for m in _LATIN_RUN.finditer(seg.text):
            span = (seg.start + m.start(), seg.start + m.end())
            words = re.findall(r"[A-Za-z][A-Za-z'’-]*", m.group())
            new = [w for w in words if w.casefold() not in source_words]
            if _inside(span, skip):
                counts["LATIN"] -= sum(map(len, words))
            elif len(new) < 2:
                counts["LATIN"] -= sum(map(len, words))  # names and terms from the source, single loanwords
            else:
                unexcused.append((span, m.group()))
        letters = sum(v for v in counts.values() if v > 0)
        if not letters:
            continue
        considered += 1
        top, top_n = max(((s, n) for s, n in counts.items() if n > 0), key=lambda x: x[1])
        if top not in expected and top_n / letters >= 0.5:
            kind = "untranslated" if top == "LATIN" else "other_script"
            out.append(Candidate("script", "wrong_language", "translation", (seg.start, seg.end), top_n / letters,
                                 {"kind": kind, "found": top, "expected": sorted(expected), "text": seg.text},
                                 "major", standalone=True))
        else:
            for span, text in unexcused:
                out.append(Candidate("script", "wrong_language", "translation", span, len(text) / len(seg.text),
                                     {"kind": "untranslated", "text": text}, "minor"))
    wrong = [c for c in out if c.standalone]
    if wrong and len(wrong) == considered:  # every sentence is in the wrong language
        for c in wrong:
            c.severity = "critical"
    return out


# --- 2. Numbers, dates, units ----------------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_ORDINALS = {w: i for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
                                        "twelfth".split(), start=1)}
_WORD_VALUES = {**_ONES, **_TENS, **_ORDINALS}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 10**5, "lakhs": 10**5, "crore": 10**7, "crores": 10**7,
           "million": 10**6, "billion": 10**9}


def canonical(number: str) -> str:
    """'05' -> '5', '1000000.50' -> '1000000.5'."""
    whole, _, frac = number.partition(".")
    whole, frac = whole.lstrip("0") or "0", frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def numbers(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Numeric tokens with their original spans. Native digits and digit grouping are normalized
    first; dates and times split into parts (2023-05-01 -> 2023, 5, 1). Math, code and URLs are skipped."""
    n = normalize(text)
    skip = masked(text)
    out = []
    for m in _NUMBER.finditer(n.text):
        span = n.to_original(*m.span())
        if not _inside(span, skip):
            out.append((canonical(m.group()), span))
    return out


def number_words(text: str) -> list[str]:
    """Values of English number words: 'fifty' -> 50, 'two thousand and five' -> 2005, 'fifth' -> 5."""
    values, total, current = [], 0, None

    def flush():
        nonlocal total, current
        if current is not None or total:
            values.append(str(total + (current or 0)))
        total, current = 0, None

    words = re.findall(r"[a-z]+", text.lower().replace("-", " "))
    for k, w in enumerate(words):
        if w in _WORD_VALUES:
            v = _WORD_VALUES[w]
            if current is not None and ((v >= 10 and current % 100) or (v < 10 and current % 10)):
                flush()  # "twenty twenty" is two numbers
            current = (current or 0) + v
            if w in _ORDINALS:
                flush()
        elif w in _SCALES and (current is not None or total):
            if _SCALES[w] == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * _SCALES[w]
                current = None
        elif w == "and" and (current is not None or total) and k + 1 < len(words) and \
                (words[k + 1] in _ONES or words[k + 1] in _TENS):
            continue
        else:
            flush()
    flush()
    return values


# Units: forms that count as the unit in a translation (symbols survive) or a back-translation (words).
UNITS = {
    "°C": ("°c", "celsius", "centigrade"), "°F": ("°f", "fahrenheit"), "%": ("%", "percent", "per cent"),
    "km": ("km", "kilomet"), "kg": ("kg", "kilogram"), "mg": ("mg", "milligram"), "ml": ("ml", "millilit"),
    "cm": ("cm", "centimet"), "mm": ("mm", "millimet"), "₹": ("₹", "rupee", "rs"), "$": ("$", "dollar"),
}
_UNIT_IN_SOURCE = {
    u: re.compile(r"\d\s*" + re.escape(u) + r"(?![A-Za-z])" if u not in ("₹", "$") else re.escape(u) + r"\s*\d")
    for u in UNITS
}


def _has_form(text: str, forms: tuple[str, ...]) -> bool:
    t = normalize(text).text.casefold()
    return any(re.search(r"(?<![a-z])" + re.escape(f), t) if f.isalpha() else f in t for f in forms)


def check_numbers(source: str, translation: str, back: str | None) -> list[Candidate]:
    """Source numbers must reach the translation; extra numbers must not appear.

    A number missing from the translation but present in the back-translation (as digits or English
    words) was written out in words: suppressed. Number words only ever excuse, never require, so
    "one of them" in a source cannot cause a false alarm.
    """
    src, tgt = numbers(source), numbers(translation)
    back_values = Counter([v for v, _ in numbers(back)] + number_words(back)) if back else Counter()
    src_words = Counter(number_words(source))
    tgt_left = Counter(v for v, _ in tgt)
    missing = []
    for value, span in src:
        if tgt_left[value]:
            tgt_left[value] -= 1
        else:
            missing.append((value, span))
    extra = []
    for value, span in tgt:
        if tgt_left[value] > 0:
            tgt_left[value] -= 1
            if src_words[value]:
                src_words[value] -= 1  # the source wrote this number in words
            else:
                extra.append((value, span))

    # "7 million" written as "70 lakh": the same quantity in another number system. Pair a missing source
    # number followed by a scale word with an extra translation number that equals it at a standard scale.
    rescaled = []
    for value, span in list(missing):
        quantity = _scaled(source, value, span)
        match = next((e for e in extra if quantity is not None and any(Decimal(e[0]) * f == quantity for f in _SCALE_FACTORS)), None)
        if match:
            missing.remove((value, span))
            extra.remove(match)
            rescaled.append(f"{value} -> {match[0]}")

    still_missing, spelled = [], []
    for value, span in missing:
        if back_values[value]:
            back_values[value] -= 1
            spelled.append(value)  # written out in words in the translation
        else:
            still_missing.append((value, span))
    candidates = []
    for k, (value, span) in enumerate(still_missing):  # pair a missing number with an extra one: it was changed
        if k < len(extra):
            got, tspan = extra[k]
            candidates.append(Candidate("numbers", "number", "translation", tspan, 1.0,
                                        {"kind": "changed", "source": value, "translation": got, "source_span": list(span)},
                                        "major", standalone=True))
        else:
            candidates.append(Candidate("numbers", "number", "source", span, 1.0,
                                        {"kind": "missing", "source": value, "back_translation_checked": back is not None},
                                        "major", standalone=back is not None))
    for got, tspan in extra[len(still_missing):]:
        candidates.append(Candidate("numbers", "number", "translation", tspan, 0.7,
                                    {"kind": "extra", "translation": got}, "major"))

    skip = masked(source)
    for unit, pattern in _UNIT_IN_SOURCE.items():
        m = next((m for m in pattern.finditer(source) if not _inside(m.span(), skip)), None)
        if m and not _has_form(translation, UNITS[unit]) and not (back and _has_form(back, UNITS[unit])):
            candidates.append(Candidate("numbers", "number", "source", m.span(), 0.8,
                                        {"kind": "unit_missing", "unit": unit}, "major", standalone=back is not None))
    for c in candidates:
        c.evidence["spelled_out_suppressed"] = spelled
        c.evidence["rescaled"] = rescaled
    return candidates


_SCALE_WORD = re.compile(r"\s*(thousand|lakhs?|million|crores?|billion)\b", re.IGNORECASE)
_SCALE_FACTORS = (10**3, 10**5, 10**6, 10**7, 10**9)  # thousand, lakh, million, crore, billion


def _scaled(source: str, value: str, span: tuple[int, int]) -> Decimal | None:
    """The quantity a source number stands for when a scale word follows it ('7 million' -> 7000000)."""
    m = _SCALE_WORD.match(source, span[1])
    if not m:
        return None
    return Decimal(value) * _SCALES[m.group(1).lower()]


# --- 3. Format: Markdown, tables, LaTeX ------------------------------------------------------

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]", re.M)
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]", re.M)
_TABLE_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.M)
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]*)[^)\n]*\)")
_EMPHASIS = re.compile(r"\*\*|__")
_LATEX_ENV = re.compile(r"\\(begin|end)\{([^}]*)\}")
_FENCE_LINE = re.compile(r"^ {0,3}```", re.M)


def _blank(text: str, regions: list[tuple[int, int]]) -> str:
    chars = list(text)
    for s, e in regions:
        for k in range(s, e):
            if chars[k] != "\n":
                chars[k] = " "
    return "".join(chars)


def _elements(text: str) -> dict[str, list[tuple[object, tuple[int, int]]]]:
    """Structural elements by kind: (key, span). Keys must match between source and translation."""
    code_blocks = [m.span() for m in _CODE_BLOCK.finditer(text)]
    prose = _blank(text, code_blocks)  # markup inside code blocks is code, not structure
    inline_code = [m.span() for m in _INLINE_CODE.finditer(prose)]
    no_code = _blank(prose, inline_code)
    maths = [m for m in _MATH.finditer(no_code)]
    plain = _blank(no_code, [m.span() for m in maths])

    def ws(s: str) -> str:
        return " ".join(s.split())

    return {
        "code_block": [(ws(text[s:e]), (s, e)) for s, e in code_blocks],
        "code_fence": [("```", m.span()) for m in _FENCE_LINE.finditer(text)],
        "inline_code": [(text[s:e], (s, e)) for s, e in inline_code],
        "math": [(ws(m.group()), m.span()) for m in maths],
        "latex_env": [(m.groups(), m.span()) for m in _LATEX_ENV.finditer(no_code)],
        "heading": [(len(m.group(1)), m.span()) for m in _HEADING.finditer(plain)],
        "list_item": [("item", m.span()) for m in _LIST_ITEM.finditer(plain)],
        "table_row": [(m.group().strip().strip("|").count("|") + 1, m.span()) for m in _TABLE_ROW.finditer(plain)],
        "link_url": [(m.group(1), m.span()) for m in _LINK.finditer(plain)],
        "emphasis": [(m.group(), m.span()) for m in _EMPHASIS.finditer(plain)],
    }


_FORMAT_SEVERITY = {"emphasis": "minor", "link_url": "minor", "heading": "minor", "list_item": "minor"}


def check_format(source: str, translation: str) -> list[Candidate]:
    """Each structural element of the source must appear, in order, in the translation."""
    src, tgt = _elements(source), _elements(translation)
    out = []
    for kind in src:
        a, b = [k for k, _ in src[kind]], [k for k, _ in tgt[kind]]
        if a == b:
            continue
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            if j2 > j1:
                span = (tgt[kind][j1][1][0], tgt[kind][j2 - 1][1][1])
            else:  # missing from the translation: somewhere between its neighbours of this kind
                span = (tgt[kind][j1 - 1][1][1] if j1 else 0, tgt[kind][j1][1][0] if j1 < len(b) else len(translation))
            out.append(Candidate("format", "format", "translation", span, 1.0,
                                 {"kind": kind, "expected": [_jsonable(k) for k in a[i1:i2]],
                                  "found": [_jsonable(k) for k in b[j1:j2]]},
                                 _FORMAT_SEVERITY.get(kind, "major"), standalone=True))
    return out


def _jsonable(key: object) -> object:
    return list(key) if isinstance(key, tuple) else key


# --- 4. Length ----------------------------------------------------------------------------------

def length_outliers(pairs: dict[object, tuple[str, str]], z_limit: float = 3.5, min_items: int = 5) -> dict[object, Candidate]:
    """Robust z-score of log(len(translation) / len(source)) among items of one language.

    `pairs` maps an item key to (source, translation), all in one language. Never flags alone.
    """
    ratios = {k: math.log(len(t) / len(s)) for k, (s, t) in pairs.items() if s.strip() and t.strip()}
    if len(ratios) < min_items:
        return {}
    med = median(ratios.values())
    # Translation length varies by about 10% between items anyway; a smaller spread (tiny or templated
    # datasets) would make every small difference look extreme.
    mad = max(median(abs(r - med) for r in ratios.values()), 0.1)
    out = {}
    for k, r in ratios.items():
        z = 0.6745 * (r - med) / mad
        if abs(z) > z_limit:
            out[k] = Candidate("length", "omission" if z < 0 else "addition", "translation",
                               (0, len(pairs[k][1])), min(1.0, abs(z) / (2 * z_limit)),
                               {"z": round(z, 2), "ratio": round(math.exp(r), 3), "median_ratio": round(math.exp(med), 3)})
    return out


# --- 5. Reference-based scores ---------------------------------------------------------------

_CHRF_PP = CHRF(word_order=2)
_BLEU = BLEU(effective_order=True)


def reference_scores(translation: str, reference: str) -> dict:
    """chrF++ and BLEU against a supplied reference, for reporting and as supporting evidence."""
    hyp, ref = normalize(translation).text, normalize(reference).text
    return {"chrf_pp": round(_CHRF_PP.sentence_score(hyp, [ref]).score, 2),
            "bleu": round(_BLEU.sentence_score(hyp, [ref]).score, 2)}


# --- 6. Names and terms -------------------------------------------------------------------------

_CAPITALIZED = re.compile(r"\b[A-Z][\w'’-]*(?:[ \t]+(?:of |de |van |von )?[A-Z][\w'’-]*)*")
_TECHNICAL = re.compile(r"\b(?:[A-Z]{2,}[\w-]*|[a-z]+[A-Z]\w*|\w*\d\w*[A-Za-z]\w*|[A-Za-z]+\d[\w-]*)\b")
_FUNCTION_WORDS = {"The", "A", "An", "In", "On", "At", "Of", "For", "From", "To", "By", "With", "And", "But",
                   "This", "That", "These", "Those", "It", "Its", "Our", "Their", "His", "Her", "When", "If"}
# Where a capitalized word is just the start of a sentence, heading, list item or table cell.
_SENTENCE_START = re.compile(r"(?:(?:^|[.?!:]\s+|\n)[ \t]*(?:(?:[-*+>|]|#{1,6}|\d+[.)])[ \t]*)*|\|[ \t]*)$")


def entities(source: str) -> list[tuple[str, tuple[int, int]]]:
    """Names and technical terms in English source text, with spans.

    ponytail: a one-word name at a sentence start ("Newton studied...") is missed, since it looks like any
    capitalized first word; the judge covers it. A dataset-wide "seen capitalized mid-sentence" list would fix it.

    Capitalized sequences (a single capitalized word at the start of a sentence is just a sentence
    start), acronyms, CamelCase and letter-digit tokens. Math, code and URLs are skipped.
    """
    skip = masked(source)
    found: dict[tuple[int, int], str] = {}
    for m in _CAPITALIZED.finditer(source):
        start, text = m.start(), m.group()
        first, _, rest = text.partition(" ")
        if first in _FUNCTION_WORDS and rest:  # "The Eiffel Tower" -> "Eiffel Tower", "In India" -> "India"
            text = rest.lstrip()
            start = m.end() - len(text)
        elif _SENTENCE_START.search(source[:start]) and " " not in text and not _TECHNICAL.fullmatch(text):
            continue  # one capitalized word at a sentence start is just the start of the sentence
        if len(text) > 1:
            found[(start, start + len(text))] = text
    for m in _TECHNICAL.finditer(source):
        if not any(s <= m.start() and m.end() <= e for s, e in found):
            found[m.span()] = m.group()
    return [(text, span) for span, text in sorted(found.items()) if not _inside(span, skip) and not text.isdigit()]


def _fuzzy_in(needle: str, hay: str, threshold: float) -> bool:
    if needle in hay:
        return True
    words, n = hay.split(), len(needle.split())
    return any(difflib.SequenceMatcher(None, needle, " ".join(words[k:k + n])).ratio() >= threshold
               for k in range(max(0, len(words) - n + 1)))


def _survives(entity: str, hay: str, threshold: float) -> bool:
    """Each capitalized word of the entity is in `hay`, allowing small spelling drift ("Meera" ~ "Mira").

    Word by word, so rewording around the name ("Polish-born French" -> "Polish-French") doesn't count
    as losing it. Terms with no capitalized word ("iPhone", "COVID-19") are matched as a phrase.
    """
    words = [normalize(w, loose=True).text for w in re.findall(r"[^\W\d_][\w'’]*", entity) if w[0].isupper()]
    if not words:
        return _fuzzy_in(normalize(entity, loose=True).text, hay, threshold)
    hay_words = set(hay.split())
    skeletons = {_skeleton(h) for h in hay_words}
    return all(w in hay_words or _skeleton(w) in skeletons
               or any(difflib.SequenceMatcher(None, w, h).ratio() >= threshold for h in hay_words)
               for w in words)


def _skeleton(word: str) -> str:
    """First letter plus consonants: romanized Indian names vary mostly in vowels (Meera / Mira -> 'mr')."""
    return word[:1] + re.sub(r"[aeiouy]", "", word[1:]) if len(word) > 2 else word


def check_entities(source: str, translation: str, back: str | None, threshold: float = 0.8) -> list[Candidate]:
    """A source name or term must survive: kept in the translation verbatim, or found in the back-translation."""
    if back is None:
        return []
    hay = normalize(back, loose=True).text + " " + normalize(translation, loose=True).text
    out = []
    for text, span in entities(source):
        if not _survives(text, hay, threshold):
            out.append(Candidate("entities", "term", "source", span, 0.6, {"entity": text}, "major"))
    return out


def run_checks(source: str, translation: str, back: str | None, expected: set[str] | None,
               reference: str | None = None) -> tuple[list[Candidate], dict]:
    """All per-item checks. Returns the candidates and the scores worth reporting."""
    candidates = (check_script(source, translation, expected) + check_numbers(source, translation, back)
                  + check_format(source, translation) + check_entities(source, translation, back))
    scores = reference_scores(translation, reference) if reference else {}
    return candidates, scores
