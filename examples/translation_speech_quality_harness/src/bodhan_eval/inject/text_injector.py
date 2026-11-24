"""Programmatic translation errors. Each function returns (new_translation, label) or None if not applicable.

Labels follow the dataset format: {"type", "severity", "span": [start, end] into the new translation,
"source_span" (optional), "annotator": "injector"}. A zero-length span marks an insertion point
(where something was deleted).
"""
from __future__ import annotations

import difflib
import random
import unicodedata

import regex

from ..text.markup import BOLD, TABLE_ROW, TABLE_SEP
from ..text.normalize import sentences
from ..text.numbers import extract_numbers

ANNOTATOR = "injector"

# English-side perturbations for mistranslation (meaning) and term (entity) errors.
MEANING_SWAPS = {
    "release": "absorb", "removes": "spreads", "before": "after", "strong": "weak", "saved": "ended", "reduces": "increases",
    "dry": "rainy", "free": "a high fee", "improves": "harms", "fertile": "barren", "adds": "removes", "switch off": "switch on",
    "rises": "sinks", "protect": "expose", "cannot": "can easily", "lower": "higher", "first": "last", "popular": "rare",
    "simple": "confusing", "important": "harmful", "survive": "fall during", "morning": "night", "young": "dead", "faster": "slower",
    "communicate": "hide", "recognise": "ignore", "healthy": "weak", "everyone": "nobody", "never": "always", "upright": "upside down",
    "deep": "wide", "float": "sink", "larger": "smaller", "quickly": "slowly", "leaving": "entering", "borrow": "buy",
}
ENTITY_SWAPS = {
    "Isaac Newton": "Galileo Galilei", "Albert Einstein": "Niels Bohr", "Ganga": "Godavari", "Himalayas": "Western Ghats",
    "Bay of Bengal": "Arabian Sea", "Varanasi": "Nashik", "Patna": "Pune", "ISRO": "NASA", "Chandrayaan-3": "Mangalyaan",
    "Moon": "Mars", "Marie Curie": "Rosalind Franklin", "Paris": "London", "UNICEF": "UNESCO", "polio": "malaria",
    "Srinivasa Ramanujan": "Aryabhata", "Erode": "Madurai", "Cambridge": "Oxford", "JavaScript": "Rust", "DNA": "RNA",
    "James Watson": "Linus Pauling", "Sun": "Moon", "Jupiter": "Saturn", "Spinach": "Carrot", "oxygen": "nitrogen",
    "carbon dioxide": "hydrogen", "wheat": "cotton", "Earth": "Mars", "Lentils": "Potatoes", "newtons": "joules",
    "Python": "Java", "WHO": "World Bank", "Constitution": "Parliament", "Friday": "Monday", "radioactivity": "electricity",
}


def find_swaps(source: str, table: dict[str, str]) -> list[tuple[str, str, int]]:
    out = []
    for a, b in table.items():
        m = regex.search(rf"(?<![A-Za-z]){regex.escape(a)}(?![A-Za-z])", source)
        if m:
            out.append((a, b, m.start()))
    return sorted(out, key=lambda t: t[2])


def perturb_source(source: str, kind: str, rng: random.Random) -> tuple[str, tuple[int, int]] | None:
    """Swap one word/name in the English source. Returns (perturbed_source, span_in_original_source)."""
    swaps = find_swaps(source, MEANING_SWAPS if kind == "mistranslation" else ENTITY_SWAPS)
    if not swaps:
        return None
    a, b, pos = rng.choice(swaps)
    return source[:pos] + b + source[pos + len(a):], (pos, pos + len(a))


def diff_span(clean: str, perturbed: str) -> tuple[int, int] | None:
    """The changed region of `perturbed` relative to `clean` (None if the whole thing was reworded)."""
    sm = difflib.SequenceMatcher(None, clean, perturbed, autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if not ops:
        return None
    start, end = ops[0][3], ops[-1][4]
    if end - start > 0.6 * max(len(perturbed), 1):
        return None
    # widen to whole words
    while start > 0 and not perturbed[start - 1].isspace():
        start -= 1
    while end < len(perturbed) and not perturbed[end].isspace() and perturbed[end] not in ".,;:!?।":
        end += 1
    return (start, max(end, start + 1))


def omission(tr: str, source: str, rng: random.Random):
    sents = sentences(tr)
    if len(sents) >= 2:
        k = rng.randrange(len(sents))
        s, e = sents[k]
        if k < len(sents) - 1:  # remove the sentence and the whitespace after it
            cut_from, cut_to = s, sents[k + 1][0]
        else:  # last sentence: remove the whitespace before it and the sentence
            cut_from, cut_to = sents[k - 1][1], e
        new = tr[:cut_from] + tr[cut_to:]
        src_sents = sentences(source)
        lab = {"type": "omission", "severity": "major", "span": [cut_from, cut_from], "annotator": ANNOTATOR, "note": "sentence deleted"}
        if len(src_sents) == len(sents):
            lab["source_span"] = list(src_sents[k])
        return new.rstrip(), lab
    commas = [m.start() for m in regex.finditer(r"[,،]", tr)]
    if commas:
        c = commas[-1]
        end = sents[0][1] if sents else len(tr)
        tail = tr[c:end]
        if len(tail.split()) >= 3:
            final = tr[end - 1] if tr[end - 1] in ".।" else ""
            new = tr[:c] + final + tr[end:]
            return new, {"type": "omission", "severity": "major", "span": [c, c], "annotator": ANNOTATOR, "note": "clause deleted"}
    return None


def addition(tr: str, donor: str, rng: random.Random):
    ds = sentences(donor)
    if not ds:
        return None
    s, e = rng.choice(ds)
    extra = donor[s:e].strip()
    sents = sentences(tr)
    if len(sents) >= 2 and rng.random() < 0.5:
        pos = sents[0][1]
        new = tr[:pos] + " " + extra + tr[pos:]
        span = [pos + 1, pos + 1 + len(extra)]
    else:
        new = tr.rstrip() + " " + extra
        span = [len(tr.rstrip()) + 1, len(new)]
    return new, {"type": "addition", "severity": "major", "span": span, "annotator": ANNOTATOR, "note": "sentence from another item inserted"}


def _shift_digit(ch: str, rng: random.Random) -> str:
    d = unicodedata.decimal(ch)
    nd = (d + rng.choice([1, 2, 3, 5, 7])) % 10
    return chr(ord(ch) - d + nd)


def number(tr: str, source: str, rng: random.Random):
    nums = extract_numbers(tr)
    if not nums:
        return None
    n = rng.choice(nums)
    digit_pos = [i for i in range(n.start, n.end) if unicodedata.category(tr[i]) == "Nd"]
    i = digit_pos[0] if len(digit_pos) == 1 or rng.random() < 0.5 else rng.choice(digit_pos)
    new = tr[:i] + _shift_digit(tr[i], rng) + tr[i + 1:]
    src_num = next((m for m in extract_numbers(source) if m.value == n.value), None)
    lab = {"type": "number", "severity": "critical", "span": [n.start, n.end], "annotator": ANNOTATOR, "note": f"digit changed in {n.raw!r}"}
    if src_num:
        lab["source_span"] = [src_num.start, src_num.end]
    return new, lab


def format_break(tr: str, rng: random.Random):
    ops = []
    rows = [m for m in TABLE_ROW.finditer(tr) if not TABLE_SEP.match(m.group(0))]
    if rows:
        ops.append("table")
    if regex.search(r"(?<!\\)\$", tr):
        ops.append("dollar")
    if len(BOLD.findall(tr)) >= 2:
        ops.append("bold")
    if regex.search(r"\\(frac|sqrt|pi|neq|rho)", tr):
        ops.append("latex_cmd")
    if regex.search(r"^#{1,6}\s", tr, regex.M):
        ops.append("heading")
    if regex.search(r"\]\(https?://", tr):
        ops.append("link")
    if not ops:
        return None
    op = rng.choice(ops)
    if op == "table":
        m = rng.choice(rows[1:] if len(rows) > 1 else rows)
        row = m.group(0)
        pipes = [i for i, c in enumerate(row) if c == "|"][1:-1]
        if not pipes:
            return None
        p = m.start() + rng.choice(pipes)
        new = tr[:p] + tr[p + 1:]
        return new, {"type": "format", "severity": "major", "span": [m.start(), m.end() - 1], "annotator": ANNOTATOR, "note": "table cell separator removed"}
    if op == "dollar":
        ds = [m.start() for m in regex.finditer(r"(?<!\\)\$", tr)]
        p = ds[-1]
        new = tr[:p] + tr[p + 1:]
        return new, {"type": "format", "severity": "major", "span": [max(0, p - 12), p], "annotator": ANNOTATOR, "note": "closing $ removed"}
    if op == "bold":
        ms = list(BOLD.finditer(tr))
        m = ms[-1]
        new = tr[:m.start()] + tr[m.end():]
        return new, {"type": "format", "severity": "major", "span": [max(0, m.start() - 12), m.start()], "annotator": ANNOTATOR, "note": "closing ** removed"}
    if op == "latex_cmd":
        m = regex.search(r"\\(frac|sqrt|pi|neq|rho)", tr)
        new = tr[:m.start()] + tr[m.start() + 1:]  # drop the backslash: \frac -> frac
        return new, {"type": "format", "severity": "major", "span": [m.start(), m.end() - 1], "annotator": ANNOTATOR, "note": "LaTeX command broken"}
    if op == "heading":
        m = regex.search(r"^(#{1,6})\s", tr, regex.M)
        new = tr[:m.start()] + tr[m.end():]
        line_end = new.find("\n", m.start())
        return new, {"type": "format", "severity": "minor", "span": [m.start(), line_end if line_end > 0 else len(new)], "annotator": ANNOTATOR,
                     "note": "heading marker removed"}
    m = regex.search(r"\]\((https?://[^)\s]+)\)", tr)
    url = m.group(1)
    new_url = url.replace("example.org", "example.com") if "example.org" in url else url + "x"
    new = tr[:m.start(1)] + new_url + tr[m.end(1):]
    return new, {"type": "format", "severity": "major", "span": [m.start(1), m.start(1) + len(new_url)], "annotator": ANNOTATOR, "note": "link URL changed"}


def untranslated(tr: str, source: str, rng: random.Random):
    """Leave one sentence (or everything) in English."""
    ts, ss = sentences(tr), sentences(source)
    # only real sentences (initials like "G. H." also split as "sentences")
    ok = [k for k in range(len(ss)) if len(source[ss[k][0]:ss[k][1]].split()) >= 4]
    if len(ts) >= 2 and len(ts) == len(ss) and ok and rng.random() < 0.6:
        k = rng.choice(ok)
        s, e = ts[k]
        eng = source[ss[k][0]:ss[k][1]]
        new = tr[:s] + eng + tr[e:]
        return new, {"type": "wrong_language", "severity": "major", "span": [s, s + len(eng)], "annotator": ANNOTATOR, "note": "sentence left in English"}
    return source, {"type": "wrong_language", "severity": "critical", "span": [0, len(source)], "annotator": ANNOTATOR, "note": "whole text left in English"}
