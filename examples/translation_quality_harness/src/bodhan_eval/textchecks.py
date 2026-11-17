"""Deterministic, model-free translation checks. Cheap, so they run on every item first.

Each check returns ``Candidate`` objects with a score in [0, 1]; whether a candidate becomes
a flag is decided later against per-category thresholds tuned on human labels.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter

from bodhan_eval.flags import Candidate
from bodhan_eval.langs import SCRIPTS
from bodhan_eval.normalize import ascii_digits, numbers, script_of

# Regions that are legitimately left in Latin script / not translated.
PROTECTED = re.compile(
    r"```.*?```|`[^`\n]*`|\$\$.*?\$\$|\$[^$\n]*\$|\\\(.*?\\\)|\\\[.*?\\\]|\\[A-Za-z]+(?:\{[^}]*\})?"
    r"|https?://\S+|\]\([^)]*\)|<[^>]+>",
    re.S,
)
WORD = re.compile(r"\S+")

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "twenty": "20", "thirty": "30",
    "fifty": "50", "hundred": "100", "thousand": "1000", "million": "1000000", "first": "1", "second": "2",
    "third": "3", "half": "2", "dozen": "12", "lakh": "100000", "crore": "10000000", "billion": "1000000000",
}

STOPWORDS = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this that
these those there their they them he she his her we our you your i not no so than then which who whom what when where
how can could will would shall should may might must do does did has have had into over under about also very more
most such only just each any some all both other same up out""".split())


def _mask_protected(text: str) -> str:
    """Replace protected regions with spaces so offsets stay aligned with the raw text."""
    return PROTECTED.sub(lambda m: " " * len(m.group(0)), text)


# ---------------------------------------------------------------------------
# Wrong language or script / untranslated text
# ---------------------------------------------------------------------------

def check_script(source: str, translation: str, lang: str) -> list[Candidate]:
    expected = SCRIPTS[lang]
    masked = _mask_protected(translation)
    words = []  # (start, end, script) for words that contain letters
    for m in WORD.finditer(masked):
        scripts = Counter(s for s in map(script_of, m.group(0)) if s)
        if scripts:
            words.append((m.start(), m.end(), scripts.most_common(1)[0][0]))
    if not words:
        if translation.strip():
            return []  # only numbers, symbols or protected regions: nothing to judge
        return [Candidate("wrong_language", 1.0, "script", "critical", (0, len(translation)), evidence={"reason": "translation is empty"})]

    foreign = [w for w in words if w[2] != expected]
    share = len(foreign) / len(words)
    if share > 0.6:
        found = Counter(w[2] for w in foreign).most_common(1)[0][0]
        return [Candidate("wrong_language", min(1.0, 0.5 + share / 2), "script", "critical", (0, len(translation)),
                          evidence={"expected_script": expected, "found_script": found, "foreign_word_share": round(share, 3)})]

    # Runs of consecutive foreign-script words inside an otherwise correct translation.
    out, run = [], []
    for w in words + [(None, None, expected)]:
        if w[2] != expected:
            run.append(w)
            continue
        if len(run) >= 2:
            text = translation[run[0][0]:run[-1][1]]
            copied = text.lower() in source.lower()
            names_only = all(translation[s:e][:1].isupper() for s, e, _ in run)
            score = min(1.0, 0.4 + 0.1 * len(run)) * (0.6 if names_only else 1.0) + (0.1 if copied else 0.0)
            out.append(Candidate("wrong_language", min(score, 1.0), "script", "major", (run[0][0], run[-1][1]),
                                 evidence={"untranslated_text": text, "script": run[0][2], "words": len(run), "copied_from_source": copied}))
        run = []
    return out


# ---------------------------------------------------------------------------
# Numbers and units
# ---------------------------------------------------------------------------

def check_numbers(source: str, translation: str) -> list[Candidate]:
    src = numbers(source)
    tgt = numbers(translation)
    src_count = Counter(v for v, _, _ in src)
    tgt_count = Counter(v for v, _, _ in tgt)
    # Numbers spelled out in English ("two") may legitimately become digits in the translation.
    spelled = Counter(NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", source.lower()) if w in NUMBER_WORDS)

    missing = src_count - tgt_count
    extra = tgt_count - src_count - spelled
    out = []
    seen: Counter = Counter()
    for value, start, end in tgt:
        seen[value] += 1
        if seen[value] > tgt_count[value] - extra[value]:
            changed = bool(missing)
            out.append(Candidate("number", 0.95 if changed else 0.7, "numbers", "major", (start, end), evidence={
                "translation_number": value, "source_numbers": sorted(src_count), "missing_from_translation": sorted(missing),
            }))
    if missing and not extra:
        for value, start, end in src:
            if missing[value] > 0:
                missing[value] -= 1
                out.append(Candidate("number", 0.6, "numbers", "major", None, (start, end), evidence={
                    "missing_number": value, "translation_numbers": sorted(tgt_count),
                }))
    return out


# ---------------------------------------------------------------------------
# Markdown / LaTeX integrity
# ---------------------------------------------------------------------------

def _lines(text: str) -> list[tuple[int, str]]:
    pos, out = 0, []
    for line in text.split("\n"):
        out.append((pos, line))
        pos += len(line) + 1
    return out


def _structure(text: str) -> dict:
    lines = _lines(text)
    math = " ".join(m.group(0) for m in re.finditer(r"\$\$.*?\$\$|\$[^$\n]*\$", text, re.S))
    return {
        "headings": [(len(m.group(1)), p) for p, line in lines if (m := re.match(r"^(#{1,6})\s", line))],
        "list_items": [p for p, line in lines if re.match(r"^\s*(?:[-*+]|\d+[.)])\s", line)],
        "table_rows": [(line.strip().count("|"), p) for p, line in lines if line.strip().startswith("|")],
        "fences": [m.start() for m in re.finditer(r"```", text)],
        "dollars": [m.start() for m in re.finditer(r"(?<!\\)\$", text)],
        "bold": [m.start() for m in re.finditer(r"\*\*", text)],
        "backticks": [m.start() for m in re.finditer(r"(?<!`)`(?!`)", text)],
        "latex_envs": Counter(re.findall(r"\\(?:begin|end)\{(\w+\*?)\}", text)),
        "latex_macros": Counter(re.findall(r"\\[A-Za-z]+", math)),
        "math_brace_balance": math.count("{") - math.count("}"),
        "links": [m.start() for m in re.finditer(r"\]\(", text)],
    }


def _line_span(text: str, pos: int) -> tuple[int, int]:
    end = text.find("\n", pos)
    return (pos, len(text) if end < 0 else end)


def check_format(source: str, translation: str) -> list[Candidate]:
    s, t = _structure(source), _structure(translation)
    out = []

    def flag(what: str, span: tuple[int, int] | None, score: float = 1.0, severity: str = "major", **ev):
        out.append(Candidate("format", score, "format", severity, span, evidence={"problem": what, **ev}))

    if [lvl for lvl, _ in s["headings"]] != [lvl for lvl, _ in t["headings"]]:
        levels_t = [lvl for lvl, _ in t["headings"]]
        bad = next((p for i, (lvl, p) in enumerate(t["headings"]) if i >= len(s["headings"]) or s["headings"][i][0] != lvl), None)
        flag("heading structure differs", _line_span(translation, bad) if bad is not None else None,
             source_levels=[lvl for lvl, _ in s["headings"]], translation_levels=levels_t)
    if s["table_rows"] or t["table_rows"]:
        widths = [w for w, _ in t["table_rows"]]
        expected = Counter(w for w, _ in s["table_rows"]).most_common(1)[0][0] if s["table_rows"] else None
        bad = next((p for w, p in t["table_rows"] if expected is not None and w != expected), None)
        if len(s["table_rows"]) != len(t["table_rows"]) or bad is not None:
            span = _line_span(translation, bad) if bad is not None else (_line_span(translation, t["table_rows"][-1][1]) if t["table_rows"] else None)
            flag("table rows or columns differ", span, source_rows=len(s["table_rows"]), translation_rows=len(t["table_rows"]), translation_pipes=widths)
    if len(s["list_items"]) != len(t["list_items"]):
        flag("list item count differs", None, 0.7, source=len(s["list_items"]), translation=len(t["list_items"]))
    markers = (("fences", "code fence"), ("dollars", "LaTeX $ delimiter"), ("bold", "** bold marker"),
               ("backticks", "inline code backtick"), ("links", "link"))
    for key, label in markers:
        if len(t[key]) != len(s[key]):
            pos = t[key][-1] if t[key] else None
            span = (pos, pos + 2) if pos is not None else None
            what = f"{label} count differs ({len(s[key])} in source, {len(t[key])} in translation)"
            if key == "bold" and t[key] and len(t[key]) % 2 == 0:
                # Still valid Markdown: word order in the target merged or split emphasised spans.
                flag(what + "; emphasis merged or split, markup still balanced", span, 0.5, "minor")
            else:
                flag(what, span)
    if s["latex_envs"] != t["latex_envs"]:
        flag("LaTeX environments differ", None, source=dict(s["latex_envs"]), translation=dict(t["latex_envs"]))
    if s["latex_macros"] != t["latex_macros"]:
        diff = (t["latex_macros"] - s["latex_macros"]) or (s["latex_macros"] - t["latex_macros"])
        macro = next(iter(diff))
        pos = translation.find(macro)
        flag(f"LaTeX macros differ ({macro})", (pos, pos + len(macro)) if pos >= 0 else None,
             missing=dict(s["latex_macros"] - t["latex_macros"]), extra=dict(t["latex_macros"] - s["latex_macros"]))
    if t["math_brace_balance"] != 0 and s["math_brace_balance"] == 0:
        flag("unbalanced braces in LaTeX", None)
    return out


# ---------------------------------------------------------------------------
# Back-translation coverage (reference-free omission / addition signal)
# ---------------------------------------------------------------------------

def stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ing", "ed", "es", "ly", "s"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            w = w[: -len(suffix)]
            break
    return w[:6]


def content_stems(text: str) -> set[str]:
    return {stem(w) for w in re.findall(r"[A-Za-z]+|\d+", ascii_digits(text)) if w.lower() not in STOPWORDS}


def clauses(text: str) -> list[tuple[int, int]]:
    """Sentence/clause spans: split on sentence ends, semicolons, colons and line breaks."""
    spans, start = [], 0
    for m in re.finditer(r"[.!?;:।\n]+(?:\s+|$)|,\s+(?=\S{0,40}\s)", text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def latin_share(text: str) -> float:
    letters = [s for s in map(script_of, _mask_protected(text)) if s]
    return sum(s == "LATIN" for s in letters) / len(letters) if letters else 0.0


def check_backtranslation(source: str, back: str) -> list[Candidate]:
    """Compare the English source with the back-translation. Clause-level coverage of content
    words separates real omissions (a whole clause gone) from paraphrase noise (scattered words)."""
    out = []
    if latin_share(back) < 0.6:
        return out  # checker failure (back-translation is not English): not evidence about the translation
    back_stems = content_stems(back)
    src_stems = content_stems(source)
    for s, e in clauses(source):
        words = content_stems(source[s:e])
        if len(words) < 3:
            continue
        coverage = len(words & back_stems) / len(words)
        if coverage < 0.6:
            out.append(Candidate("omission", 1.0 - coverage, "backtranslation", "major", None, (s, e), evidence={
                "source_clause": source[s:e], "content_word_coverage": round(coverage, 3), "back_translation": back,
            }))
    for s, e in clauses(back):
        words = content_stems(back[s:e])
        if len(words) < 3:
            continue
        coverage = len(words & src_stems) / len(words)
        if coverage < 0.4:
            out.append(Candidate("addition", (1.0 - coverage) * 0.8, "backtranslation", "major", None, None, evidence={
                "back_translated_clause": back[s:e], "content_word_coverage": round(coverage, 3),
            }))
    return out


# ---------------------------------------------------------------------------
# Entity preservation (via the back-translation)
# ---------------------------------------------------------------------------

ENTITY = re.compile(r"\b(?:[A-Z]{2,}[0-9A-Z-]*|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|[A-Za-z]+[0-9]+[A-Za-z0-9]*)\b")


def entities(text: str) -> list[tuple[str, int, int]]:
    """Names, acronyms and alphanumeric terms (NASA, Isaac Newton, H2O, COVID-19). Capitalised words that
    only start a sentence are skipped unless they are acronyms."""
    out = []
    masked = _mask_protected(text)
    for m in ENTITY.finditer(masked):
        word = m.group(0)
        sentence_start = re.search(r"(^|[.!?:\n]\s*|^\s*[-*\d.]+\s+|#+\s+|\|\s*)$", masked[:m.start()]) is not None
        if sentence_start and not word.isupper() and " " not in word:
            continue
        out.append((word, m.start(), m.end()))
    return out


def check_entities(source: str, back: str) -> list[Candidate]:
    """An entity of the source that does not come back in the back-translation was probably rendered
    wrongly. Low score on its own (back-translation may respell names); it corroborates the judge."""
    if latin_share(back) < 0.6:
        return []
    back_stems = {stem(w) for w in re.findall(r"[A-Za-z0-9]+", back)}
    out = []
    for word, s, e in entities(source):
        parts = [stem(w) for w in re.findall(r"[A-Za-z0-9]+", word)]
        if parts and not any(p in back_stems for p in parts):
            out.append(Candidate("term", 0.45, "entities", "major", None, (s, e), evidence={"entity": word, "back_translation": back}))
    return out


# ---------------------------------------------------------------------------
# Length ratio, relative to the other items of the same language in this run
# ---------------------------------------------------------------------------

def length_outliers(items: list[tuple[str, str, str]]) -> dict[str, list[Candidate]]:
    """items: (id, source, translation) for one language. Robust z-score of log length ratio;
    the reference is the batch median, so no per-language constant is hard-coded."""
    ratios = {i: math.log((len(t) + 1) / (len(s) + 1)) for i, s, t in items if s.strip()}
    if len(ratios) < 8:
        return {}
    med = statistics.median(ratios.values())
    mad = statistics.median(abs(r - med) for r in ratios.values()) or 0.05
    out: dict[str, list[Candidate]] = {}
    for item_id, r in ratios.items():
        z = (r - med) / (1.4826 * mad)
        if abs(z) >= 2.5:
            cat = "omission" if z < 0 else "addition"
            out[item_id] = [Candidate(cat, min(1.0, abs(z) / 8), "length_ratio", "major", None, None, evidence={
                "log_length_ratio": round(r, 3), "language_median": round(med, 3), "robust_z": round(z, 2),
            })]
    return out
