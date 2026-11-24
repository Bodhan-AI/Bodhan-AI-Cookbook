"""Tier 2: Bodhan round trips.

- Back-translation: translate the translation to English and look for source sentences that did not survive,
  and English sentences that have no source. Noisy: the return trip makes its own mistakes, so suspects are re-checked.
- Regeneration diff: translate the source again (greedy) and diff it with the supplied translation. Where the two
  differ is where an edit or a regression is. Trusted only as far as the run shows supplied outputs are reproducible.
"""

import difflib
import re

from .checks_text import MIN_ITEMS_FOR_STATS, cand
from .normalize import mask_protected, normalize, sentence_spans

COVERAGE_FLOOR = 0.4  # a sentence whose content words are under 40% present on the other side is a suspect
AGREEMENT_HIGH = 0.8  # share of items reproduced exactly above which the diff counts as exact evidence
_STOP = set("the a an of to in on at by for and or but is are was were be been being it its this that these those with as from "
            "into than then so such which who whom whose what when where while about over under also not no can could will would "
            "shall should may might must has have had do does did".split())
_TOKEN = re.compile(r"[^\s।॥.,;:!?()\[\]\"'|“”‘’]+|[।॥.,;:!?()\[\]\"'|“”‘’]")


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", normalize(mask_protected(text))[0]) if w not in _STOP and (len(w) > 2 or w.isdigit())]


def _present(word: str, bag: set[str]) -> bool:
    # Shared prefix tolerates English inflection (pumps/pump, arteries/artery) without a stemmer.
    if word in bag:
        return True
    for b in bag:
        n = max(4, min(len(word), len(b)) - 2)
        if min(len(word), len(b)) >= 4 and word[:n] == b[:n]:
            return True
    return False


def coverage(words: list[str], other: list[str]) -> float:
    if not words:
        return 1.0
    bag = set(other)
    return sum(_present(w, bag) for w in words) / len(words)


def _sentences(text):
    """Sentences with any letter or digit (drops table separator rows and stray punctuation), any script."""
    return [(s, e) for s, e in sentence_spans(text) if any(ch.isalnum() for ch in text[s:e])]


def _proportional(i, n_from, n_to):
    return min(n_to - 1, round(i * n_to / n_from)) if n_from and n_to else None


# ---------------------------------------------------------------- back-translation

def back_translation(item, bodhan, ctx=None) -> dict:
    src, tr = item["source"], item["translation"]
    bt = bodhan.translate(tr, "en", ctx=ctx)["text"]
    cands = _bt_candidates(src, tr, bt)
    if any(c["category"] == "omission" for c in cands):
        cands = _recheck_omissions(src, tr, bt, cands, bodhan, ctx)
    return {"back_translation": bt, "candidates": cands}


def _bt_candidates(src, tr, bt):
    s_sents, b_sents, t_sents = _sentences(src), _sentences(bt), _sentences(tr)
    bt_words, src_words = _content_words(bt), _content_words(src)
    cands = []
    for s, e in s_sents:
        cov = coverage(_content_words(src[s:e]), bt_words)
        if cov < COVERAGE_FLOOR:
            cands.append(cand("back_translation", "omission", "noisy", round(1 - cov, 3), (s, e), None, None,
                              detail="source sentence not found in the back-translation", coverage=round(cov, 2), source=src[s:e]))
    for j, (s, e) in enumerate(b_sents):
        cov = coverage(_content_words(bt[s:e]), src_words)
        if cov < COVERAGE_FLOOR:
            k = _proportional(j, len(b_sents), len(t_sents))
            cands.append(cand("back_translation", "addition", "noisy", round(1 - cov, 3), None, t_sents[k] if k is not None else None, None,
                              detail="back-translated sentence has no counterpart in the source", coverage=round(cov, 2), back_translation=bt[s:e]))
    return cands


def _recheck_omissions(src, tr, bt, cands, bodhan, ctx):
    """Back-translate the translation sentence by sentence. If the 'missing' sentence shows up, the first back-translation lost it."""
    pieces = [bodhan.translate(tr[s:e], "en", ctx=ctx)["text"] for s, e in _sentences(tr)]
    words = _content_words(" ".join(pieces))
    kept = []
    for c in cands:
        if c["category"] == "omission":
            s, e = c["source_span"]
            cov = coverage(_content_words(src[s:e]), words)
            if cov >= COVERAGE_FLOOR:
                continue  # checker noise: the content is there
            c["evidence"]["recheck_coverage"] = round(cov, 2)
            c["strength"] = round(min(1.0, c["strength"] + 0.1), 3)
        kept.append(c)
    return kept


# ---------------------------------------------------------------- regeneration diff

def _tokens(text):
    return [(m.start(), m.end(), normalize(m.group())[0]) for m in _TOKEN.finditer(text)]


def regenerate(item, bodhan, ctx=None) -> dict:
    """Fresh greedy translation and its token diff with the supplied one (classification happens after the run's agreement is known)."""
    fresh = bodhan.translate(item["source"], item["lang"], ctx=ctx)["text"]
    a, b = _tokens(item["translation"]), _tokens(fresh)
    sm = difflib.SequenceMatcher(a=[t[2] for t in a], b=[t[2] for t in b], autojunk=False)
    regions = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        t_span = (a[i1][0], a[i2 - 1][1]) if i2 > i1 else ((a[i1 - 1][1], a[i1 - 1][1]) if i1 else (0, 0))
        regions.append({"op": op, "translation_span": t_span, "supplied": item["translation"][t_span[0]:t_span[1]],
                        "fresh": fresh[b[j1][0]:b[j2 - 1][1]] if j2 > j1 else "", "fresh_span": (b[j1][0], b[j2 - 1][1]) if j2 > j1 else None})
    sim = difflib.SequenceMatcher(a=normalize(item["translation"])[0], b=normalize(fresh)[0], autojunk=False).ratio()
    return {"fresh": fresh, "similarity": round(sim, 4), "regions": regions}


def agreement(regens: list[dict]) -> float | None:
    """Share of items whose supplied translation is reproduced exactly. None when too few items to say."""
    if len(regens) < MIN_ITEMS_FOR_STATS:
        return None
    return round(sum(not r["regions"] for r in regens) / len(regens), 3)


def regen_candidates(item, regen, agree) -> list[dict]:
    if not regen["regions"]:
        return []
    cls = "exact" if agree is not None and agree >= AGREEMENT_HIGH else "noisy"
    strength = 0.9 if cls == "exact" else (0.6 if agree is None else round(0.3 + 0.5 * agree, 3))
    src, tr, fresh = item["source"], item["translation"], regen["fresh"]
    if regen["similarity"] < 0.5:  # not a local edit: a different system, language or content
        return [cand("regen_diff", "mistranslation", "noisy", strength * 0.7, None, (0, len(tr)), None,
                     detail="supplied translation differs throughout from a fresh translation", similarity=regen["similarity"], agreement=agree)]
    s_sents, t_sents, f_sents = _sentences(src), _sentences(tr), _sentences(fresh)
    cands = []
    for r in regen["regions"]:
        if r["op"] == "insert":
            category = "omission"
            # Which source sentence went missing: the fresh sentence holding most of the missing words, at the same index in the source.
            fs, fe = r["fresh_span"]
            overlaps = [min(e, fe) - max(s, fs) for s, e in f_sents]
            k = max(range(len(overlaps)), key=overlaps.__getitem__) if overlaps and max(overlaps) > 0 else None
            s_span = s_sents[_proportional(k, len(f_sents), len(s_sents))] if k is not None and s_sents else None
        else:
            s_span = None
            if r["op"] == "delete":
                category = "addition"
            elif re.search(r"\d", normalize(r["supplied"] + r["fresh"])[0]):
                category = "number"
            elif re.search(r"[A-Za-z]", r["supplied"]):
                category = "term" if re.search(r"[A-Za-z]", r["fresh"]) or _has_term(src) else "wrong_language"
            else:
                category = "mistranslation"
            k = next((i for i, (s, e) in enumerate(t_sents) if s <= r["translation_span"][0] < e), None)
            s_span = s_sents[_proportional(k, len(t_sents), len(s_sents))] if k is not None and s_sents else None
        t_span = r["translation_span"]
        if t_span[0] == t_span[1]:  # pure insertion point: report the sentence around it
            k = next((i for i, (s, e) in enumerate(t_sents) if s <= t_span[0] <= e), None)
            t_span = t_sents[k] if k is not None else t_span
        cands.append(cand("regen_diff", category, cls, strength, s_span, t_span, None, detail=f"differs from a fresh translation ({r['op']})",
                          supplied=r["supplied"][:300], fresh=r["fresh"][:300], agreement=agree))
    return cands


def _has_term(text: str) -> bool:
    """Capitalised word not at a sentence start, or an acronym: a likely name or term."""
    for s, e in sentence_spans(text):
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", text[s:e])
        if any(w.isupper() and len(w) > 1 for w in words) or any(w[0].isupper() for w in words[1:]):
            return True
    return False
