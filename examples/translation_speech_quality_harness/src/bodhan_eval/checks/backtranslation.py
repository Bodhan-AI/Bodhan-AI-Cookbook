"""Back-translation check: translate the output back to English and compare with the source.

Each translation sentence is back-translated on its own, so every English back-translation
(BT) segment maps to exactly one translation sentence. Flags therefore carry exact spans on
the translation instead of a proportional guess.

Steps:
  1. Align source sentences with BT segments (monotonic, chrF similarity, 1-1 / 2-1 / 1-2).
  2. Unmatched source sentence            -> omission candidate.
     Unmatched BT segment                 -> addition candidate.
     Both unmatched at the same position  -> mistranslation candidate (meaning replaced).
  3. Inside matched pairs: a contiguous run of source content words missing from the BT
     (or BT words missing from the source) -> partial omission / addition candidate.
  4. Source entities missing from the BT  -> term candidate.
  5. Numbers: which source numbers survive in the BT. `reconcile_numbers` uses this to
     raise or lower the deterministic number check's confidence.

Back-translation is noisy (paraphrase, synonyms, transliterated names), so every
confidence here is modest: these are candidates for the combiner and the judge.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Protocol

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from bodhan_eval.align import align_segments
from bodhan_eval.checks.base import CheckResult, Flag
from bodhan_eval.checks.reference import similarity
from bodhan_eval.checks.translation import _EN_NUMBER_WORDS, source_entities
from bodhan_eval.languages import Language
from bodhan_eval.normalize import Token, extract_numbers, script_of, split_sentences, tokenize
from bodhan_eval.params import Params

NAME = "backtranslation"
EN = "en"

_STOPWORDS = set(
    "a an the this that these those is are was were be been being am do does did done have has "
    "had having in on at of for from by with to into onto over under about as than then so and "
    "or but nor if when while because which who whom whose what where why how it its it's "
    "our their his her my your we they he she i you me him them us there here also very just "
    "not no can could will would shall should may might must such each any all some more most "
    "other only own same too s t".split()
)


class Translator(Protocol):
    async def translate(self, text: str, target_language: str, script: str = ...): ...


def _has_letters(text: str) -> bool:
    return any(script_of(ch) for ch in text)


def _content(tokens: list[Token]) -> list[int]:
    """Indices of content-word tokens (not stopwords, not pure punctuation)."""
    return [i for i, t in enumerate(tokens) if t.norm not in _STOPWORDS and len(t.norm) > 1]


def _word_match(w: str, vocab: set[str]) -> bool:
    """Lenient English word match: exact, shared 5-char stem, or near spelling."""
    if w in vocab:
        return True
    for v in vocab:
        if len(w) >= 5 and len(v) >= 5 and w[:5] == v[:5]:
            return True
        if Levenshtein.normalized_similarity(w, v) >= 0.8:
            return True
    return False


def _missing_run(
    tokens: list[Token], other_vocab: set[str], min_run: int
) -> tuple[tuple[int, int] | None, float, list[str]]:
    """Longest contiguous stretch of content words absent from `other_vocab`.

    Stopwords inside the stretch do not break it. Returns (char span, coverage of all
    content words, missing words in the stretch); span is None if the stretch is short.
    """
    content = _content(tokens)
    if not content:
        return None, 1.0, []
    found = [_word_match(tokens[i].norm, other_vocab) for i in content]
    coverage = sum(found) / len(found)
    best: tuple[int, int] = (0, 0)  # [start, end) over `content` positions
    start = None
    for k, ok in enumerate([*found, True]):
        if not ok and start is None:
            start = k
        elif ok and start is not None:
            if k - start > best[1] - best[0]:
                best = (start, k)
            start = None
    if best[1] - best[0] < min_run:
        return None, coverage, []
    first, last = content[best[0]], content[best[1] - 1]
    words = [tokens[content[k]].text for k in range(best[0], best[1])]
    return (tokens[first].start, tokens[last].end), coverage, words


def _acronym_ok(entity: str, bt: str) -> bool:
    """'ISRO' is fine if the BT spells it out ('Indian Space Research Organisation')."""
    if not (entity.isupper() and 2 <= len(entity) <= 6):
        return False
    caps = [w for w in re.findall(r"[A-Za-z]+", bt) if w[0].isupper() and w.lower() != "of"]
    initials = "".join(w[0] for w in caps).upper()
    return entity in initials


_SCALES = {
    "hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "million": 10**6,
    "crore": 10**7, "crores": 10**7, "billion": 10**9, "trillion": 10**12,
}  # fmt: skip
_SCALE_RE = re.compile(r"^\s*(" + "|".join(_SCALES) + r")\b", re.I)


def _scaled(text: str, n) -> str | None:
    """Value of a number followed by an English scale word ('400 million' -> '400000000')."""
    m = _SCALE_RE.match(text[n.end : n.end + 14])
    if not m:
        return None
    v = float(n.value) * _SCALES[m.group(1).lower()]
    return str(int(v)) if v.is_integer() else str(v)


def _bt_number_values(bt: str) -> set[str]:
    """Numbers in the English back-translation: as written, as number words, and with
    scale words applied ('40 crore' also counts as 400000000)."""
    nums = extract_numbers(bt)
    vals = {n.value for n in nums} | {v for n in nums if (v := _scaled(bt, n))}
    low = bt.lower()
    vals |= {v for w, v in _EN_NUMBER_WORDS.items() if re.search(rf"\b{w}\b", low)}
    return vals


def _source_number_status(source: str, bt_vals: set[str]) -> tuple[list[str], list[str]]:
    """Which source numbers survive in the BT, comparing scaled values too, so
    '400 million' in the source matches '40 crore' in the BT (an Indian-system rewrite)."""
    found, missing = set(), set()
    for n in extract_numbers(source):
        sc = _scaled(source, n)
        (found if n.value in bt_vals or (sc and sc in bt_vals) else missing).add(n.value)
    return sorted(found), sorted(missing - found)


class _BatchStats:
    """Batching saves requests only if the model keeps one output line per input line.
    After a few attempts, stop batching for this client if it mostly merges lines."""

    def __init__(self, min_tries: int = 4, min_rate: float = 0.5):
        self.tries = self.ok = 0
        self.min_tries, self.min_rate = min_tries, min_rate

    def worth_trying(self) -> bool:
        return self.tries < self.min_tries or self.ok / self.tries >= self.min_rate

    def record(self, ok: bool) -> None:
        self.tries += 1
        self.ok += ok


def _batch_stats(client) -> _BatchStats:
    st = getattr(client, "_bt_batch_stats", None)
    if st is None:
        st = _BatchStats()
        with contextlib.suppress(AttributeError):  # client without attributes: no memory
            client._bt_batch_stats = st
    return st


async def back_translate(
    client: Translator, translation: str
) -> tuple[list[tuple[int, int]], list[str], bool, str]:
    """Back-translate the translation sentence by sentence.

    Returns (sentence spans, BT per sentence, truncated, mode). Segments without letters
    (table rules, pure math) are kept as they are. To save requests under the API's rate
    limit, all sentences go in ONE request, one per line, and the reply is split by line;
    if the line count does not match, each sentence is sent on its own. Sentences already
    cached individually are reused for free.
    """
    spans = split_sentences(translation)
    texts = [translation[s:e] for s, e in spans]
    out = list(texts)
    todo = [i for i, t in enumerate(texts) if _has_letters(t)]
    if not todo:
        return spans, out, False, "none"

    lookup = getattr(client, "cached_translate", None)
    if lookup is not None:
        hits = [lookup(texts[i], EN) for i in todo]
        if all(hits):
            for i, h in zip(todo, hits, strict=True):
                out[i] = h.text
            return spans, out, any(h.truncated for h in hits), "cached"

    stats = _batch_stats(client)
    if len(todo) > 1 and stats.worth_trying():
        joined = "\n".join(" ".join(texts[i].split()) for i in todo)
        r = await client.translate(joined, EN)
        lines = [ln.strip() for ln in r.text.split("\n") if ln.strip()]
        ok = len(lines) == len(todo) and not r.truncated
        stats.record(ok)
        if ok:
            for i, ln in zip(todo, lines, strict=True):
                out[i] = ln
            return spans, out, False, "batched"

    async def one(i: int):
        r = await client.translate(texts[i], EN)
        return i, r.text, bool(r.truncated)

    truncated = False
    for i, text, tr in await asyncio.gather(*(one(i) for i in todo)):
        out[i] = text
        truncated |= tr
    return spans, out, truncated, "per_sentence"


def _t_span(t_spans: list[tuple[int, int]], idx: tuple[int, ...]) -> tuple[int, int]:
    return (t_spans[idx[0]][0], t_spans[idx[-1]][1])


def _snip(t: str, n: int = 80) -> str:
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 1] + "…"


async def check_backtranslation(
    client: Translator, source: str, translation: str, lang: Language, params: Params
) -> CheckResult:
    if not translation.strip():
        return CheckResult.skipped(NAME, "empty translation")
    if not source.strip():
        return CheckResult.skipped(NAME, "empty source")
    p = params.section("backtranslation")
    min_sim = p.get("match_min_chrf", 30.0)
    min_words = p.get("omission_min_content_words", 3)
    min_run = p.get("partial_min_missing_run", 4)
    max_cov = p.get("partial_coverage_max", 0.6)
    ent_sim = p.get("entity_min_similarity", 85)

    t_spans, bt, truncated, bt_mode = await back_translate(client, translation)
    s_spans = split_sentences(source)
    s_txt = [source[s:e] for s, e in s_spans]
    pairs = align_segments(s_txt, bt, similarity, min_sim=min_sim)

    res = CheckResult(NAME)
    bt_full = " ".join(bt)
    # Source sentence index -> translation sentence indices it aligned to.
    s_to_t: dict[int, tuple[int, ...]] = {}
    for pr in pairs:
        for i in pr.a:
            if pr.b:
                s_to_t[i] = pr.b

    def nearest_t(pos: int) -> tuple[int, ...]:
        """Translation sentence next to where an unmatched pair-list position sits."""
        for k in range(pos - 1, -1, -1):
            if pairs[k].b:
                return (pairs[k].b[-1],)
        for k in range(pos + 1, len(pairs)):
            if pairs[k].b:
                return (pairs[k].b[0],)
        return (0,)

    # --- 2. whole-sentence omission / addition / replacement -----------------------------
    k = 0
    while k < len(pairs):
        if pairs[k].a and pairs[k].b:
            k += 1
            continue
        # A run of gaps between two matched anchors.
        j = k
        while j < len(pairs) and not (pairs[j].a and pairs[j].b):
            j += 1
        gap = pairs[k:j]
        src_idx = [i for g in gap for i in g.a]
        bt_idx = [i for g in gap for i in g.b]
        if src_idx and bt_idx:
            _flag_replacement(res, source, s_spans, src_idx, bt, bt_idx, t_spans)
        elif src_idx:
            for i in src_idx:
                words = len(_content(tokenize(s_txt[i])))
                if not _has_letters(s_txt[i]):
                    continue
                t_idx = nearest_t(k)
                res.flags.append(
                    Flag(
                        "omission",
                        "major" if words >= min_words else "minor",
                        NAME,
                        0.6 if words >= min_words else 0.3,
                        _t_span(t_spans, t_idx),
                        f"source sentence “{_snip(s_txt[i])}” has no counterpart in the "
                        "back-translation",
                        source_span=s_spans[i],
                        details={"span_kind": "adjacent_sentence", "content_words": words},
                    )
                )
        else:
            for i in bt_idx:
                words = len(_content(tokenize(bt[i])))
                if not _has_letters(bt[i]):
                    continue
                if _covered_by_neighbours(bt[i], k, j, pairs, s_txt):
                    continue  # the system split a source sentence: not new content
                res.flags.append(
                    Flag(
                        "addition",
                        "major" if words >= min_words else "minor",
                        NAME,
                        0.55 if words >= min_words else 0.3,
                        t_spans[i],
                        f"translation sentence back-translates to “{_snip(bt[i])}”, which "
                        "matches nothing in the source",
                        details={"back_translation": bt[i], "content_words": words},
                    )
                )
        k = j

    # --- 3. partial omission / addition inside matched pairs -----------------------------
    for pr in pairs:
        if not (pr.a and pr.b):
            continue
        s_off = s_spans[pr.a[0]][0]
        src_seg = source[s_off : s_spans[pr.a[-1]][1]]
        bt_seg = " ".join(bt[i] for i in pr.b)
        s_tok, b_tok = tokenize(src_seg), tokenize(bt_seg)
        s_vocab, b_vocab = {t.norm for t in s_tok}, {t.norm for t in b_tok}
        t_span = _t_span(t_spans, pr.b)

        run, cov, words = _missing_run(s_tok, b_vocab, min_run)
        if run and cov <= max_cov:
            res.flags.append(
                Flag(
                    "omission",
                    "minor",
                    NAME,
                    0.4,
                    t_span,
                    f"“{' '.join(words)}” from the source is missing in the back-translation "
                    f"“{_snip(bt_seg)}”",
                    source_span=(s_off + run[0], s_off + run[1]),
                    details={"coverage": round(cov, 2), "pair_chrf": round(pr.score, 1)},
                )
            )
        run, cov, words = _missing_run(b_tok, s_vocab, min_run)
        if run and cov <= max_cov:
            res.flags.append(
                Flag(
                    "addition",
                    "minor",
                    NAME,
                    0.35,
                    t_span,
                    f"back-translation adds “{' '.join(words)}”, not in the source sentence "
                    f"“{_snip(src_seg)}”",
                    source_span=(s_off, s_spans[pr.a[-1]][1]),
                    details={"coverage": round(cov, 2), "pair_chrf": round(pr.score, 1)},
                )
            )

    # --- 4. entities ---------------------------------------------------------------------
    bt_low = bt_full.lower()
    t_low = translation.lower()
    ents_checked = 0
    for ent, (s, e) in source_entities(source):
        si = next((i for i, (a, b) in enumerate(s_spans) if a <= s < b), None)
        if si is None or si not in s_to_t:
            continue  # sentence already reported as omitted / replaced
        ents_checked += 1
        el = ent.lower()
        if el in t_low or fuzz.partial_ratio(el, bt_low) >= ent_sim or _acronym_ok(ent, bt_full):
            continue
        t_idx = s_to_t[si]
        res.flags.append(
            Flag(
                "term",
                "major",
                NAME,
                0.45,
                _t_span(t_spans, t_idx),
                f"“{ent}” from the source does not appear in the back-translation "
                f"“{_snip(' '.join(bt[i] for i in t_idx))}”",
                source_span=(s, e),
                details={"entity": ent},
            )
        )

    # --- 5. numbers (signals for reconcile_numbers) ----------------------------------------
    in_bt, missing_bt = _source_number_status(source, _bt_number_values(bt_full))

    if truncated:
        for f in res.flags:
            f.confidence = round(f.confidence * 0.5, 3)
            f.details["bt_truncated"] = True

    res.signals = {
        "bt_chrf": round(similarity(source, bt_full), 2),
        "back_translation": bt,
        "bt_truncated": truncated,
        "bt_mode": bt_mode,
        "aligned_pairs": [[list(pr.a), list(pr.b), round(pr.score, 1)] for pr in pairs],
        "entities_checked": ents_checked,
        "source_numbers_in_bt": in_bt,
        "source_numbers_missing_in_bt": missing_bt,
    }
    return res


def _covered_by_neighbours(
    seg: str, k: int, j: int, pairs: list, s_txt: list[str], min_cov: float = 0.7
) -> bool:
    """Are the content words of an unmatched BT segment already in the source sentences
    aligned right before / after the gap? Then the translation split one source sentence
    into several (1 -> 2 or 1 -> 3), and the extra piece is not an addition."""
    near = [i for pr in (pairs[k - 1] if k else None, pairs[j] if j < len(pairs) else None)
            if pr and pr.a and pr.b for i in pr.a]  # fmt: skip
    if not near:
        return False
    vocab = {t.norm for i in near for t in tokenize(s_txt[i])}
    toks = tokenize(seg)
    content = [toks[i].norm for i in _content(toks)]
    if not content:
        return True
    return sum(_word_match(w, vocab) for w in content) / len(content) >= min_cov


def _flag_replacement(
    res: CheckResult,
    source: str,
    s_spans: list[tuple[int, int]],
    src_idx: list[int],
    bt: list[str],
    bt_idx: list[int],
    t_spans: list[tuple[int, int]],
) -> None:
    """Source and BT sentences both unmatched in the same place: the translation says
    something there, but not what the source said."""
    src_text = " ".join(source[s_spans[i][0] : s_spans[i][1]] for i in src_idx)
    bt_text = " ".join(bt[i] for i in bt_idx)
    sim = similarity(src_text, bt_text)
    res.flags.append(
        Flag(
            "mistranslation",
            "major",
            NAME,
            0.5,
            _t_span(t_spans, tuple(bt_idx)),
            f"source “{_snip(src_text)}” back-translates as “{_snip(bt_text)}”",
            source_span=(s_spans[src_idx[0]][0], s_spans[src_idx[-1]][1]),
            details={"chrf": round(sim, 1), "back_translation": bt_text},
        )
    )


def reconcile_numbers(numbers: CheckResult, bt: CheckResult) -> None:
    """Adjust the deterministic number check using back-translation evidence (in place).

    A number missing from the translation but present in the BT was most likely written
    out in words, so confidence goes down. Missing from both: confidence goes up.
    Substitutions (a different number in its place) are already high-confidence; they are
    only nudged.
    """
    if not (numbers.ran and bt.ran):
        return
    in_bt = set(bt.signals.get("source_numbers_in_bt", []))
    missing_bt = set(bt.signals.get("source_numbers_missing_in_bt", []))
    for f in numbers.flags:
        exp = f.details.get("expected")
        if exp is None:
            continue
        if exp in in_bt:
            f.confidence = round(f.confidence * 0.5, 3)
            f.details["bt"] = "number present in back-translation (likely spelled out)"
        elif exp in missing_bt:
            f.confidence = round(min(0.95, f.confidence + 0.15), 3)
            f.details["bt"] = "number also missing from back-translation"
