"""Error injector (plan §15): copies of translations with one known, exactly labelled error each.

    python -m bodhan_eval.inject --in report/items.jsonl --out data/injected.jsonl --per-category 20

Input: JSONL with id, lang, source, translation (a report's items.jsonl, or a dataset with translations).
Output: every input item unchanged (the *base*), then the variants. A variant's id is `<base id>~<what>`,
it records `base` and `injection`, and carries one label with `annotator: "injector"`. Hard negatives
(harmless changes: digits, Unicode form, punctuation, spacing) carry `injection.category = "clean"` and
no labels. Bases are included so each variant can be compared with its own base: the bases are not
guaranteed clean, and only what a variant adds over its base says anything about the injected error.

Indic text is only ever cut, moved or copied from existing translations, or produced by Indic-Translate.
The LLM only edits English (for mistranslation and term errors), which Indic-Translate then translates.
Seeded: the same input, seed and cache give the same output.

Severity by rule: format errors in headings, lists or emphasis are minor, other format errors major;
an omission removing under 15% of the text is minor; a whole translation in the wrong language is
critical; everything else is major.
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import random
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .checks import entities, masked
from .clients import BudgetExceeded, Clients, ModelError
from .config import Config, env_settings, load_dotenv
from .langs import script_counts
from .segment import _MATH, segments

log = logging.getLogger("bodhan_eval.inject")

CATEGORIES = ("omission", "addition", "number", "format", "wrong_language", "mistranslation", "term")
UNITS = ("°C", "°F", "%", "km", "kg", "cm", "mm", "mg", "ml", "₹", "$")
MAX_LLM_WORDS = 6  # an LLM edit changing more English words than this is not "one phrase": rejected


def label(category, severity, span, op, **detail):
    return {"type": category, "severity": severity, "side": "translation", "span": list(span),
            "annotator": "injector", "op": op, **({"detail": detail} if detail else {})}


def _prose(rec):
    """Translated segments that are plain prose (not a table row or heading), where text can be cut or added."""
    t = rec["translation"]
    out = []
    for s in segments(t):
        line = t[t.rfind("\n", 0, s.start) + 1:t.find("\n", s.end) % (len(t) + 1)]
        if "|" not in line and not line.lstrip().startswith("#"):
            out.append(s)
    return out


def _paired(rec):
    """(source segment, translated segment) pairs when both sides split into the same number of segments."""
    src, tgt = segments(rec["source"]), segments(rec["translation"])
    return list(zip(src, tgt)) if len(src) == len(tgt) else []


def _list_markers(text):
    return [m.span() for m in re.finditer(r"^[ \t]*\d+[.)]", text, re.M)]


def _severity_omission(removed, text):
    return "minor" if len(removed.strip()) < 0.15 * len(text.strip()) else "major"


# --- operations: each returns (new translation, label) or None if it doesn't apply -------------------

def omission(rec, rng, **_):
    t, segs = rec["translation"], _prose(rec)
    if len(segments(t)) >= 2 and segs:
        s = rng.choice(segs)
        end = s.end + len(t[s.end:]) - len(t[s.end:].lstrip(" \t"))  # take the spaces after it too
        new, removed = t[:s.start] + t[end:], t[s.start:end]
        return new, label("omission", _severity_omission(removed, t), (s.start, s.start), "delete_sentence", deleted=removed)
    # One sentence: cut a clause, from a comma to the next comma or the sentence's last character.
    clauses = []
    for s in segs:
        commas = [m.start() for m in re.finditer(r"[,،;]", s.text)]
        for i, c in enumerate(commas):
            stop = commas[i + 1] if i + 1 < len(commas) else len(s.text.rstrip(".?!।॥ "))
            if len(s.text[c:stop].split()) >= 3:
                clauses.append((s.start + c, s.start + stop))
    if not clauses:
        return None
    a, b = rng.choice(clauses)
    return t[:a] + t[b:], label("omission", _severity_omission(t[a:b], t), (a, a), "delete_clause", deleted=t[a:b])


def addition(rec, rng, pool, **_):
    t, segs = rec["translation"], _prose(rec)
    donors = [d for d in pool.get(rec["lang"], []) if d[0] != rec["source"] and len(d[1].split()) >= 4]
    if not segs or not donors:
        return None
    s, (_, sentence) = rng.choice(segs), rng.choice(donors)
    new = t[:s.end] + " " + sentence + t[s.end:]
    return new, label("addition", "major", (s.end + 1, s.end + 1 + len(sentence)), "insert_sentence", inserted=sentence)


def number(rec, rng, **_):
    t = rec["translation"]
    skip = masked(t) + _list_markers(t)  # "1." -> "6." renumbers a list; it isn't a number error
    digits = [m for m in re.finditer(r"\d+", t) if not any(a <= m.start() < b for a, b in skip)]
    units = [m for u in UNITS for m in re.finditer(r"(?<=\d)\s?" + re.escape(u), t)]
    if not digits:
        return None
    if units and rng.random() < 0.3:
        m = rng.choice(units)
        return t[:m.start()] + t[m.end():], label("number", "major", (m.start(), m.start()), "drop_unit", unit=m.group().strip())
    m = rng.choice(digits)
    i = rng.randrange(m.start(), m.end())
    d = unicodedata.digit(t[i])
    zero = ord(t[i]) - d
    new_d = rng.choice([x for x in range(10) if x != d and not (x == 0 and i == m.start() and m.end() - m.start() > 1)])
    return t[:i] + chr(zero + new_d) + t[i + 1:], label("number", "major", (i, i + 1), "change_digit", was=m.group(),
                                                       now=t[m.start():i] + chr(zero + new_d) + t[i + 1:m.end()])


_FORMAT_OPS = {
    # name: (pattern, group whose text is deleted, severity)
    "drop_heading": (re.compile(r"^(#{1,6}[ \t]+)", re.M), 1, "minor"),
    "drop_list_marker": (re.compile(r"^[ \t]*(\d+[.)][ \t]+|[-*+][ \t]+)", re.M), 1, "minor"),
    "drop_closing_bold": (re.compile(r"\*\*[^*\n]+(\*\*)"), 1, "minor"),
    "drop_table_pipe": (re.compile(r"^\|[^\n|]+(\|)(?=[^\n]*\|)", re.M), 1, "major"),
}


def format_error(rec, rng, **_):
    t = rec["translation"]
    found = [(op, m, g, sev) for op, (p, g, sev) in _FORMAT_OPS.items() for m in p.finditer(t)
             if not (op == "drop_table_pipe" and set(m.group()) <= set("|-: \t"))]
    found += [("unclose_math", m, None, "major") for m in _MATH.finditer(t) if m.group().startswith("$") and not m.group().startswith("$$")]
    if not found:
        return None
    op, m, g, sev = rng.choice(found)
    a, b = (m.end() - 1, m.end()) if g is None else m.span(g)  # unclose_math: drop the closing "$"
    return t[:a] + t[b:], label("format", sev, (a, a), op, deleted=t[a:b])


def wrong_language(rec, rng, by_key, **_):
    t = rec["translation"]
    choices = []
    for s, g in _paired(rec):
        choices.append(("english", s.text, g))
    for other in by_key.get(rec["id"], {}).values():
        if other["lang"] != rec["lang"] and other.get("translation"):
            theirs, ours = segments(other["translation"]), segments(t)
            if len(theirs) == len(ours):
                choices += [(f"copy_{other['lang']}", x.text, g) for x, g in zip(theirs, ours)]
    if not choices:
        return None
    op, text, g = rng.choice(choices)
    whole = len(segments(t)) == 1
    return t[:g.start] + text + t[g.end:], label("wrong_language", "critical" if whole else "major",
                                                 (g.start, g.start + len(text)), op)


# --- LLM-assisted: edit one English phrase, let Indic-Translate translate it --------------------------

PROMPTS = {
    "mistranslation": (
        "You make test cases for a translation checker. Change the meaning of the English sentence below by "
        "replacing ONE word or short phrase (at most 4 words), so it still reads naturally but says something "
        "different: an antonym (rose -> fell, exports -> imports), the wrong person or thing, or adding or removing "
        "a negation. Do not change numbers, names or Markdown/LaTeX. Keep every other word exactly the same."),
    "term": (
        "You make test cases for a translation checker. In the English sentence below, replace ONE name or "
        "technical term with a different real one of the same kind (another person, place, organisation, "
        "substance, species...), e.g. Chennai -> Mumbai, photosynthesis -> respiration. Do not change numbers or "
        "Markdown/LaTeX. Keep every other word exactly the same."),
}


def llm_edit(category):
    def op(rec, rng, clients, **_):
        pairs = _paired(rec)
        if category == "term":
            names = entities(rec["source"])
            pairs = [(s, g) for s, g in pairs if any(s.start <= a < s.end for _, (a, _) in names)]
        if not pairs:
            return None
        s, g = rng.choice(pairs)
        hint = ""
        if category == "term":
            hint = "\nTerms in it: " + ", ".join(n for n, (a, _) in entities(rec["source"]) if s.start <= a < s.end)
        reply = clients.llm([{"role": "user", "content": PROMPTS[category] + hint +
                              '\nReply with JSON only: {"sentence": "<edited sentence>", "from": "<replaced words>", '
                              f'"to": "<new words>"}}\n\nSentence: {s.text}'}])
        edited = (reply or {}).get("sentence")
        if not isinstance(edited, str) or not _small_english_edit(s.text, edited):
            log.info("%s/%s: unusable %s edit: %r", rec["id"], rec["lang"], category, edited)
            return None
        new_seg = clients.translate(edited, rec["lang"]).text.strip()
        old_seg = g.text
        if new_seg == old_seg:
            return None
        a, b = _diff_span(old_seg, new_seg)
        new = rec["translation"][:g.start] + new_seg + rec["translation"][g.end:]
        return new, label(category, "major", (g.start + a, g.start + b), "llm_edit_retranslate",
                          english_before=s.text, english_after=edited, changed=f"{reply.get('from')} -> {reply.get('to')}")
    return op


def _small_english_edit(before, after):
    if after.strip() == before.strip() or set(script_counts(after)) - {"LATIN"}:
        return False
    a, b = before.split(), after.split()
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in
                  difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal")
    return 1 <= changed <= MAX_LLM_WORDS


def _diff_span(old, new):
    """[start, end) in `new` covering everything that differs from `old`, widened to whole words."""
    ops = [o for o in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if o[0] != "equal"]
    a, b = ops[0][3], ops[-1][4]
    while a > 0 and not new[a - 1].isspace():
        a -= 1
    while b < len(new) and not new[b].isspace():
        b += 1
    return a, b


# --- hard negatives: changes that must not be reported -------------------------------------------------

def _native_zero(text):
    script = next((s for s, _ in script_counts(text).most_common() if s != "LATIN"), None)
    try:
        return ord(unicodedata.lookup(f"{script} DIGIT ZERO")) if script else None
    except KeyError:
        return None


def hard_negative(rec, rng, **_):
    t = rec["translation"]
    ops = []
    # List markers ("1. ") need ASCII digits in Markdown, so converting those would be a real format error.
    zero, skip = _native_zero(t), masked(t) + _list_markers(t)
    if zero and any(c.isascii() and c.isdigit() for c in t):
        ops.append(("native_digits", "".join(
            chr(zero + int(c)) if c.isascii() and c.isdigit() and not any(a <= i < b for a, b in skip) else c
            for i, c in enumerate(t))))
    if unicodedata.normalize("NFD", t) != t:
        ops.append(("nfd", unicodedata.normalize("NFD", t)))
    if "।" in t:
        ops.append(("danda_to_period", t.replace("।", ".")))
    if '"' in t:
        ops.append(("curly_quotes", re.sub(r'"([^"\n]*)"', "“\\1”", t)))
    spaces = [m.start() for m in re.finditer(r"(?<=\S) (?=\S)", t)]
    if spaces:
        i = rng.choice(spaces)
        ops.append(("extra_space", t[:i] + "  " + t[i + 1:] + " "))
    ops = [(op, new) for op, new in ops if new != t]
    return rng.choice(ops) if ops else None


OPS = {"omission": omission, "addition": addition, "number": number, "format": format_error,
       "wrong_language": wrong_language, "mistranslation": llm_edit("mistranslation"), "term": llm_edit("term")}


def inject(records, clients, per_category=20, negatives=20, seed=42, categories=CATEGORIES):
    bases = [r for r in records if r.get("translation")]
    by_key: dict[str, dict] = defaultdict(dict)
    pool: dict[str, list] = defaultdict(list)
    for r in bases:
        by_key[r["id"]][r["lang"]] = r
        pool[r["lang"]] += [(r["source"], s.text) for s in _prose(r)]
    ctx = {"by_key": by_key, "pool": pool, "clients": clients}
    variants = []
    for what in (*categories, "clean"):
        want = negatives if what == "clean" else per_category
        rng = random.Random(f"{seed}:{what}")
        got = 0
        for rec in _interleave_langs(bases, rng):
            if got == want:
                break
            op_rng = random.Random(f"{seed}:{what}:{rec['id']}:{rec['lang']}")
            try:
                out = (hard_negative if what == "clean" else OPS[what])(rec, op_rng, **ctx)
            except BudgetExceeded:
                log.warning("budget reached: stopping %s injections at %d", what, got)
                break
            except ModelError as e:
                log.warning("%s/%s: %s injection failed: %s", rec["id"], rec["lang"], what, e)
                continue
            if out is None:
                continue
            if what == "clean":
                op, new = out
                variants.append(_variant(rec, new, {"category": "clean", "op": op}, [], f"clean-{op}"))
            else:
                new, lab = out
                variants.append(_variant(rec, new, {"category": what, "op": lab["op"], "severity": lab["severity"]}, [lab], what))
            got += 1
        log.info("%s: %d of %d wanted", what, got, want)
    keep = ("id", "lang", "source", "translation", "reference", "translation_origin", "kind", "domain")
    return [{k: r[k] for k in keep if r.get(k) is not None} for r in bases] + variants


def _variant(rec, translation, injection, labels, suffix):
    return {"id": f"{rec['id']}~{suffix}", "lang": rec["lang"], "source": rec["source"], "translation": translation,
            "translation_origin": rec.get("translation_origin"), "base": rec["id"], "injection": injection, "labels": labels}


def _interleave_langs(records, rng):
    """Records in a seeded random order that alternates languages, so each category is spread across them."""
    by_lang = defaultdict(list)
    for r in records:
        by_lang[r["lang"]].append(r)
    queues = [rng.sample(v, len(v)) for _, v in sorted(by_lang.items())]
    while any(queues):
        for q in queues:
            if q:
                yield q.pop()


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.inject")
    p.add_argument("--in", dest="inp", type=Path, required=True, help="JSONL with id, lang, source, translation")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--per-category", type=int, default=20)
    p.add_argument("--negatives", type=int, default=20, help="hard negatives (harmless changes) to add")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-cost-inr", type=float, default=None)
    args = p.parse_args(argv)
    load_dotenv()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx2").setLevel("WARNING")
    cfg = Config(dataset=args.inp, langs=(), out=args.out, max_cost_inr=args.max_cost_inr, **env_settings(os.environ))
    clients = Clients(cfg, Path(".bodhan_cache"))
    records = [json.loads(line) for line in args.inp.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        rows = inject(records, clients, args.per_category, args.negatives, args.seed)
    finally:
        clients.close()
    with args.out.open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    n = sum(1 for r in rows if "base" in r)
    print(f"wrote {len(rows) - n} bases and {n} variants to {args.out}; cost {json.dumps(clients.ledger.summary()['by_model'])}")


if __name__ == "__main__":
    main()
