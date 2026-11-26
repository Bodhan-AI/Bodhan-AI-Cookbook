"""Translation checks. Each function returns plain JSON-able *signals*; turning signals into
flags happens in decide.py with tunable thresholds (so calibration costs no model calls)."""
from __future__ import annotations

import difflib
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

from sacrebleu.metrics import CHRF

from .align import split_sentences
from .clients import Services
from .normalize import (
    ascii_digits,
    expected_script,
    has_letters,
    mask_markup,
    normalize_text,
    script_of,
    tokenize,
)

# ---------------------------------------------------------------- numbers / units
_NUM = re.compile(r"(?<!\w)(?<!\d\.)\d+(?:[.,]\d+)*")  # "Rs.45" counts; ".5" inside 3.5 does not
_UNIT = re.compile(r"\s{0,2}(°\s?[CFK]|%|km/h|kmph|m/s|km|kg|cm|mm|mg|ml|khz|mhz|ghz|hz|kb|mb|gb|tb|[mgl])(?![A-Za-z])", re.IGNORECASE)
_ORDINAL = re.compile(r"^(st|nd|rd|th)\b", re.IGNORECASE)


def _canon_num(s: str) -> str | None:
    s = s.replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return format(d.normalize(), "f")


def extract_numbers(text: str) -> list[dict]:
    t = ascii_digits(text)  # same length -> offsets valid for the original
    out = []
    for m in _NUM.finditer(t):
        val = _canon_num(m.group())
        if val is None:
            continue
        after = t[m.end(): m.end() + 3]
        if _ORDINAL.match(after):
            continue  # ordinals are often spelled out in translation
        line_start = t.rfind("\n", 0, m.start()) + 1
        if t[line_start: m.start()].strip() == "" and re.match(r"[.)]\s", t[m.end(): m.end() + 2]):
            continue  # list marker "1. "
        um = _UNIT.match(t, m.end())
        unit = re.sub(r"\s", "", um.group(1)).lower() if um else None
        out.append({"value": val, "span": [m.start(), m.end()], "unit": unit})
    return out


def number_signal(source: str, translation: str) -> dict:
    src, tr = extract_numbers(source), extract_numbers(translation)
    used = set()
    unmatched_src, unit_changes = [], []
    for s in src:
        for j, t in enumerate(tr):
            if j not in used and t["value"] == s["value"]:
                used.add(j)
                if s["unit"] and t["unit"] and s["unit"] != t["unit"]:
                    unit_changes.append({"src": s, "tr": t})
                break
        else:
            unmatched_src.append(s)
    unmatched_tr = [t for j, t in enumerate(tr) if j not in used]
    changed = []
    while unmatched_src and unmatched_tr:
        changed.append({"src": unmatched_src.pop(0), "tr": unmatched_tr.pop(0)})
    return {"changed": changed, "missing": unmatched_src, "extra": unmatched_tr, "unit_changes": unit_changes}


# ---------------------------------------------------------------- script / language
def script_signal(source: str, translation: str, lang: str) -> dict:
    masked = mask_markup(translation)
    letters = [c for c in masked if has_letters(c)]
    src_letters = [c for c in mask_markup(source) if has_letters(c)]
    exp = expected_script(lang)
    sig = {"expected": exp, "n_letters": len(letters), "frac_expected": None, "latin_run": None, "same_as_source": False}
    if not letters or not src_letters:
        return sig
    scripts = [script_of(c) for c in letters]
    if exp:
        sig["frac_expected"] = round(sum(1 for s in scripts if s == exp) / len(letters), 4)
    else:  # unseen language code: anything non-Latin counts as "in language"
        sig["frac_expected"] = round(sum(1 for s in scripts if s != "Latn") / len(letters), 4)
    sig["same_as_source"] = normalize_text(source) == normalize_text(translation)
    # longest run of >=3 consecutive Latin words => partially untranslated text
    run, best, best_span = [], 0, None
    for tok in tokenize(masked):
        if all(script_of(c) == "Latn" for c in tok.raw if has_letters(c)) and any(has_letters(c) for c in tok.raw):
            run.append(tok)
            if len(run) > best:
                best, best_span = len(run), [run[0].start, run[-1].end]
        else:
            run = []
    if best >= 3:
        sig["latin_run"] = {"words": best, "span": best_span}
    return sig


# ---------------------------------------------------------------- Markdown / LaTeX integrity
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LATEX_CMD = re.compile(r"\\[A-Za-z]+")


def _profile(text: str) -> dict:
    lines = text.split("\n")
    table_cols = []
    for ln in lines:
        if "|" in ln and ln.strip():
            table_cols.append(ln.strip().strip("|").count("|") + 1)
    math_blocks = re.findall(r"\$\$.*?\$\$|\$[^$\n]+\$|\\\(.*?\\\)|\\\[.*?\\\]", text, re.DOTALL)
    return {
        "fences": text.count("```"),
        "headings": sum(1 for ln in lines if re.match(r"\s{0,3}#{1,6}\s", ln)),
        "bullets": sum(1 for ln in lines if re.match(r"\s*([-*+]|\d+[.)])\s", ln)),
        "table_rows": len(table_cols),
        "table_seps": sum(1 for ln in lines if "|" in ln and _TABLE_SEP.match(ln)),
        "table_cols": table_cols,
        "dollars": text.replace("$$", "").count("$"),
        "dbl_dollars": text.count("$$"),
        "bold": text.count("**"),
        "links": len(re.findall(r"\]\(", text)),
        "latex_cmds": sorted(Counter(_LATEX_CMD.findall(" ".join(math_blocks))).items()),
        "brace_balance": [max(0, b.count("{") - b.count("}")) + max(0, b.count("}") - b.count("{")) for b in math_blocks],
    }


def format_signal(source: str, translation: str) -> list[dict]:
    """Differences in structural markup between source and translation. Empty list = intact."""
    ps, pt = _profile(source), _profile(translation)
    issues: list[dict] = []

    def line_span(pattern_fn):
        pos = 0
        for ln in translation.split("\n"):
            if pattern_fn(ln):
                return [pos, pos + len(ln)]
            pos += len(ln) + 1
        return None

    if ps["fences"] != pt["fences"] or pt["fences"] % 2:
        issues.append({"kind": "code_fence", "detail": f"code fences {ps['fences']} -> {pt['fences']}", "span": line_span(lambda l: "```" in l)})
    if ps["headings"] != pt["headings"]:
        issues.append({"kind": "headings", "detail": f"headings {ps['headings']} -> {pt['headings']}", "span": None})
    if ps["table_rows"] != pt["table_rows"] or ps["table_seps"] != pt["table_seps"]:
        issues.append({"kind": "table", "detail": f"table rows {ps['table_rows']} -> {pt['table_rows']}, separators {ps['table_seps']} -> {pt['table_seps']}", "span": line_span(lambda l: "|" in l)})
    elif ps["table_cols"] != pt["table_cols"]:
        issues.append({"kind": "table_columns", "detail": f"column counts {ps['table_cols']} -> {pt['table_cols']}", "span": line_span(lambda l: "|" in l)})
    if ps["dollars"] % 2 == 0 and pt["dollars"] % 2 == 1 or ps["dbl_dollars"] != pt["dbl_dollars"]:
        issues.append({"kind": "math_delimiter", "detail": f"math delimiters '$' {ps['dollars']} -> {pt['dollars']}, '$$' {ps['dbl_dollars']} -> {pt['dbl_dollars']}", "span": line_span(lambda l: "$" in l)})
    if ps["latex_cmds"] != pt["latex_cmds"]:
        issues.append({"kind": "latex_commands", "detail": f"LaTeX commands changed {ps['latex_cmds']} -> {pt['latex_cmds']}", "span": line_span(lambda l: "$" in l or "\\" in l)})
    if sum(pt["brace_balance"]) > sum(ps["brace_balance"]):
        issues.append({"kind": "latex_braces", "detail": "unbalanced braces inside math", "span": line_span(lambda l: "{" in l or "}" in l)})
    if pt["bold"] % 2 and not ps["bold"] % 2:
        issues.append({"kind": "bold_marker", "detail": "unclosed ** emphasis", "span": line_span(lambda l: "**" in l)})
    if ps["links"] != pt["links"]:
        issues.append({"kind": "links", "detail": f"links {ps['links']} -> {pt['links']}", "span": None})
    if ps["bullets"] != pt["bullets"]:
        issues.append({"kind": "list_items", "detail": f"list items {ps['bullets']} -> {pt['bullets']}", "span": None})
    return issues


# ---------------------------------------------------------------- length prior
def length_ratio(source: str, translation: str) -> float | None:
    a = len([c for c in mask_markup(source) if has_letters(c)])
    b = len([c for c in mask_markup(translation) if has_letters(c)])
    return round(b / a, 4) if a >= 10 else None


# ---------------------------------------------------------------- reference-based
_chrf = CHRF(word_order=2)


def chrf_score(hyp: str, ref: str) -> float:
    return round(_chrf.sentence_score(normalize_text(hyp), [normalize_text(ref)]).score / 100, 4)


# ---------------------------------------------------------------- back-translation
_STOP = set(["a", "an", "the", "of", "to", "in", "on", "at", "by", "for", "with", "from", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those", "is", "are", "was", "were", "be", "been", "being", "it", "its", "as", "into", "over", "under", "about", "not", "no", "do", "does", "did", "has", "have", "had", "will", "would", "can", "could", "may", "might", "also", "such", "there", "their", "they", "them", "he", "she", "his", "her", "we", "you", "your", "our", "i", "me", "my", "which", "who", "whom", "what", "when", "where", "while", "so", "up", "out"])


def stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def content_tokens(text: str):
    """[(stem, token)] for non-stopword tokens."""
    return [(stem(t.text), t) for t in tokenize(text) if t.text not in _STOP and (len(t.text) >= 3 or t.text.isdigit())]


def _covered(st: str, pool: set[str]) -> bool:
    if st in pool:
        return True
    if len(st) < 5:
        return False
    return any(len(p) >= 5 and abs(len(p) - len(st)) <= 3 and difflib.SequenceMatcher(None, st, p).ratio() >= 0.8 for p in pool)


def _entities(sentence: str, offset: int):
    toks = tokenize(sentence)
    out = []
    for i, t in enumerate(toks):
        r = t.raw
        if not r.isascii() or not r[0].isalpha():
            continue
        if (i > 0 and r[0].isupper() and len(r) > 2) or (r.isupper() and len(r) >= 2):
            out.append((t.text, [offset + t.start, offset + t.end]))
    return out


def back_translate(translation: str, lang: str, svc: Services) -> list[dict]:
    segs = []
    for s, e in split_sentences(translation):
        seg = translation[s:e]
        bt = None
        if has_letters(mask_markup(seg)):
            bt = svc.translate(seg, lang, "en")
        segs.append({"span": [s, e], "bt": bt})
    return segs


def bt_signal(source: str, bt_segments: list[dict], ref_bt: list[dict] | None = None) -> dict:
    bt_text = " ".join(s["bt"] for s in bt_segments if s["bt"])
    bt_pool = {stem(t.text) for t in tokenize(bt_text)}
    bt_raw = {t.text for t in tokenize(bt_text)}
    src_pool = {stem(t.text) for t in tokenize(source)}
    ref_pool = None
    if ref_bt:
        ref_pool = {stem(t.text) for t in tokenize(" ".join(s["bt"] for s in ref_bt if s["bt"]))}
    src_sents = []
    for s, e in split_sentences(source):
        ct = content_tokens(source[s:e])
        if len(ct) < 2:
            continue
        unc = [t for st, t in ct if not _covered(st, bt_pool)]
        cov = 1 - len(unc) / len(ct)
        cov_ref = None
        if ref_pool is not None:
            cov_ref = 1 - len([1 for st, _ in ct if not _covered(st, ref_pool)]) / len(ct)
        ents = [(w, sp) for w, sp in _entities(source[s:e], s) if not _covered(stem(w), bt_pool) and w not in bt_raw]
        src_sents.append({
            "span": [s, e], "coverage": round(cov, 4),
            "coverage_ref": None if cov_ref is None else round(cov_ref, 4),
            "uncovered": [[s + t.start, s + t.end] for t in unc][:6],
            "n_uncovered": len(unc),
            "uncovered_words": [t.raw for t in unc][:6],
            "entities_missing": [{"word": w, "span": sp} for w, sp in ents],
        })
    tr_sents = []
    for seg in bt_segments:
        if not seg["bt"]:
            continue
        ct = content_tokens(seg["bt"])
        if len(ct) < 2:
            continue
        extra = [t.raw for st, t in ct if not _covered(st, src_pool)]
        tr_sents.append({"span": seg["span"], "extra_frac": round(len(extra) / len(ct), 4),
                         "extra_words": extra[:6], "bt": seg["bt"]})
    return {"back_translation": bt_text, "src_sents": src_sents, "tr_sents": tr_sents,
            "src_all": [list(x) for x in split_sentences(source)], "tr_all": [s["span"] for s in bt_segments]}
