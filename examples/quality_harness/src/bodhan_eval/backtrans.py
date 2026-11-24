"""Back-translation check with checker-noise control (plan §10).

Indic-Translate's back-translation makes its own mistakes. To separate them from real errors, every
signal is also measured on a control: Indic-Translate's own fresh translation of the same source,
back-translated the same way. A signal counts only when the system is clearly worse than the control
on that sentence. The control can only weaken or remove a candidate, never create one, so this does
not reduce to "differs from Indic-Translate".

Flow per item:
1. Whole-item back-translation of the system (one call) and of the control (two calls: translate, back).
2. Candidates from the system alignment, each checked against the control.
3. Only if there are candidates: sentence-by-sentence back-translation as a second opinion. A
   candidate survives only if both back-translations show it, and it gains a span in the translation.

When the translation is our own generated one (or identical to the fresh translation), the control
is the same text and says nothing; the second opinion is then the only noise check.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .align import GAP, Alignment, Pair, align_item, align_item_by_segment, project_span, word_diff
from .checks import Candidate
from .clients import Clients, ModelError
from .normalize import normalize

log = logging.getLogger("bodhan_eval.backtrans")

# A weak pair counts when the control beats the system on that sentence by more than this.
# First-principles default (a clear, not marginal, gap); calibration (§14) replaces it.
DELTA = 0.15

_STOPWORDS = set("""
a an the and or but if then than so as of in on at to for from by with without into onto over under about
is are was were be been being am do does did done have has had having will would shall should can could may
might must it its this that these those there here he she they them his her their we us our you your i me my
not no nor very also just only too more most less least such same other another each every all any some
which who whom whose what when where why how while because although though up down out off again further
""".split())


@dataclass
class BackTranslationResult:
    candidates: list[Candidate]
    control: str  # "independent", "same_as_system" or "unavailable"
    suppressed: list[dict] = field(default_factory=list)  # candidates dropped as checker noise, with the reason
    system: Alignment | None = None
    control_alignment: Alignment | None = None
    second: Alignment | None = None  # the sentence-by-sentence back-translation, when it was made

    @property
    def back_texts(self) -> list[str]:
        """Every back-translation made of the system output: evidence that content survived."""
        return [a.back_text for a in (self.system, self.second) if a is not None and a.back_text]


def check_back_translation(source: str, translation: str, lang: str, clients: Clients, generated: bool = False,
                           gap: float = GAP, delta: float = DELTA) -> BackTranslationResult:
    system = align_item(source, translation, clients)
    control, kind = None, "same_as_system"
    if not generated:
        try:
            fresh = clients.translate(source, lang).text
            if normalize(fresh).text != normalize(translation).text:
                control, kind = align_item(source, fresh, clients), "independent"
        except ModelError as e:
            log.warning("control translation failed, continuing without it: %s", e)
            kind = "unavailable"

    suppressed: list[dict] = []
    first = _candidates(system, control, gap, delta, suppressed)
    result = BackTranslationResult([], kind, suppressed, system, control)
    if not first:
        return result

    try:
        second_al = align_item_by_segment(source, translation, clients)
    except ModelError as e:
        log.warning("sentence-by-sentence back-translation failed; keeping unconfirmed candidates: %s", e)
        for c in first:
            c.score *= 0.5
            c.evidence["confirmed"] = False
        result.candidates = first
        return result

    result.second = second_al
    second = _candidates(second_al, control, gap, delta, [])
    for c in second:
        if any(_same_problem(c, f) for f in first):
            c.evidence["confirmed"] = True
            _add_translation_span(c, second_al)
            if kind != "independent":
                c.score *= 0.6  # only the second opinion vouches for it
            result.candidates.append(c)
    for f in first:
        if not any(_same_problem(c, f) for c in second):
            suppressed.append({"category": f.category, "evidence": f.evidence,
                               "reason": "not seen in the sentence-by-sentence back-translation"})
    return result


def _candidates(al: Alignment, control: Alignment | None, gap: float, delta: float, suppressed: list[dict]) -> list[Candidate]:
    ctl_score = _scores_by_source(control) if control else {}
    ctl_back = _back_by_source(control) if control else {}
    ctl_extra = _unmatched_back_chars(control) if control else 0
    out: list[Candidate] = []

    for p in al.pairs:
        src_text = " ".join(al.source[k].text for k in p.src)
        back_text = " ".join(al.back[k].text for k in p.back)
        span = (al.source[p.src[0]].start, al.source[p.src[-1]].end) if p.src else None
        base = {"src_idx": list(p.src), "back_idx": list(p.back), "source_text": src_text, "back_translation": back_text,
                "system_score": p.score}
        if p.src:
            base["control_score"] = round(min(ctl_score.get(k, 0.0) for k in p.src), 4) if control else None
            base["control_back_translation"] = " ".join(dict.fromkeys(ctl_back.get(k, "") for k in p.src)).strip() or None

        if p.src and not p.back:  # omission
            if control and base["control_score"] < gap:
                suppressed.append({"category": "omission", "evidence": base, "reason": "the control loses this sentence too"})
            else:
                out.append(Candidate("back_translation", "omission", "source", span, 0.8, base))
        elif p.back and not p.src:  # addition
            if control and ctl_extra >= 0.5 * len(back_text):
                suppressed.append({"category": "addition", "evidence": base, "reason": "the control's back-translation adds text too"})
            else:
                out.append(Candidate("back_translation", "addition", "translation", (0, 0), 0.7, base))
        else:
            weak = p.score < gap if control is None else (base["control_score"] or 0.0) - p.score > delta
            if weak:
                if control:
                    base["margin"] = round((base["control_score"] or 0.0) - p.score, 4)
                # Classify by the changes the control doesn't share (rewording the control's back-translation
                # also shows is the checker's habit). Only words taken away: the sentence lost content.
                changes = _system_changes(src_text, back_text, base.get("control_back_translation") if control else None)
                category = "omission" if changes and all(tag == "delete" for tag, _, _ in changes) else "mistranslation"
                base["changes"] = [{"change": t, "source_words": src_text[sp[0]:sp[1]] if sp else None,
                                    "back_translation_has": w or None} for t, sp, w in changes]
                out.append(Candidate("back_translation", category, "source", span, 0.5, base))
            elif control:
                # "The system lost words the control kept" needs a control: without one, every paraphrase
                # ("costs" -> "fee is") would look like a missing phrase.
                out.extend(_missing_phrases(al, p, base, suppressed))
            out.extend(_negation(base, span, control, suppressed))
    return out


# Negation words. One "not" barely moves chrF and is a stopword for the phrase check, yet reverses the meaning.
_NEGATION = re.compile(r"\b(?:not|no|never|none|nobody|nothing|nowhere|neither|nor|without|cannot)\b|n't\b", re.I)


def _negation(base: dict, span, control: Alignment | None, suppressed: list[dict]) -> list[Candidate]:
    """The back-translation has more or fewer negations than the source sentence (and than the control's)."""
    n_src, n_back = len(_NEGATION.findall(base["source_text"])), len(_NEGATION.findall(base["back_translation"]))
    if n_src == n_back:
        return []
    evidence = {**base, "negations": {"source": n_src, "back_translation": n_back}}
    ctl = base.get("control_back_translation")
    if control and ctl is not None:
        evidence["negations"]["control"] = n_ctl = len(_NEGATION.findall(ctl))
        if n_ctl == n_back:
            suppressed.append({"category": "mistranslation", "evidence": evidence,
                               "reason": "the control's back-translation has the same negations"})
            return []
    return [Candidate("back_translation_negation", "mistranslation", "source", span, 0.7, evidence, "major")]


def _content_words(text: str) -> list[str]:
    return [w for w in normalize(text, loose=True).text.split() if w not in _STOPWORDS and not w.isdigit()]


def _system_changes(src_text: str, back_text: str, control_back: str | None) -> list[tuple[str, tuple[int, int] | None, str]]:
    """word_diff changes, minus those none of whose source words the control's back-translation has."""
    changes = word_diff(src_text, back_text).changes
    if control_back is None:
        return changes
    kept = {_stem(w) for w in normalize(control_back, loose=True).text.split()}
    out = []
    for tag, span, words in changes:
        content = _content_words(src_text[span[0]:span[1]]) if span else []
        if any(_stem(w) in kept for w in content):
            out.append((tag, span, words))
    return out


def _missing_phrases(al: Alignment, p: Pair, base: dict, suppressed: list[dict]) -> list[Candidate]:
    """Content words of the source sentence absent from its back-translation (but kept by the control's)."""
    out = []
    wd = word_diff(base["source_text"], base["back_translation"])
    control_words = {_stem(w) for w in normalize(base.get("control_back_translation") or "", loose=True).text.split()}
    for tag, span, back_words in wd.changes:
        if span is None:
            continue
        text = base["source_text"][span[0]:span[1]]
        content = _content_words(text)
        if not content:
            continue
        # word_diff spans are offsets into the joined source text; map them back to the item's source
        offset = _joined_offset(al, p, span[0])
        item_span = (offset, offset + span[1] - span[0])
        evidence = {**base, "missing_words": text, "back_translation_has": back_words or None, "change": tag}
        if not any(_stem(w) in control_words for w in content):
            suppressed.append({"category": "phrase", "evidence": evidence,
                               "reason": "the control's back-translation lacks these words too"})
            continue
        category = "omission" if tag == "delete" else "mistranslation"
        out.append(Candidate("back_translation_phrase", category, "source", item_span, 0.6, evidence))
    return out


def _joined_offset(al: Alignment, p: Pair, pos: int) -> int:
    """Offset in the joined source text of a pair (sentences joined by one space) -> offset in the item's source."""
    for k in p.src:
        seg = al.source[k]
        if pos <= len(seg.text):
            return seg.start + pos
        pos -= len(seg.text) + 1
    return al.source[p.src[-1]].end


def _scores_by_source(al: Alignment) -> dict[int, float]:
    return {k: (p.score if p.back else 0.0) for p in al.pairs for k in p.src}


def _back_by_source(al: Alignment) -> dict[int, str]:
    return {k: " ".join(al.back[j].text for j in p.back) for p in al.pairs for k in p.src}


def _unmatched_back_chars(al: Alignment) -> int:
    return sum(len(al.back[j].text) for p in al.pairs if not p.src for j in p.back)


def _same_problem(a: Candidate, b: Candidate) -> bool:
    """The same candidate found by both back-translations: same category, overlapping source sentences."""
    if a.category != b.category or a.check != b.check:
        return False
    if a.category == "addition":
        return True  # the two back-translations index their sentences differently; one addition each is agreement
    if a.check == "back_translation_phrase":
        return a.span[0] < b.span[1] and b.span[0] < a.span[1]
    return bool(set(a.evidence["src_idx"]) & set(b.evidence["src_idx"]))


def _add_translation_span(c: Candidate, al: Alignment) -> None:
    """With a sentence-by-sentence alignment, point the candidate at the translation too."""
    assert al.target is not None
    if c.category == "addition":
        targets = [al.target[j] for j in c.evidence["back_idx"]]
        c.span = (targets[0].start, targets[-1].end)
        return
    t = project_span(al, *c.span)
    if t is not None:
        c.evidence["translation_span"] = list(t)
    if c.check == "back_translation" and c.category in ("mistranslation", "omission") and t is not None \
            and t[0] < t[1]:  # a weak pair: the sentence exists in the translation, so point at it
        c.evidence["source_span"] = list(c.span)
        c.side, c.span = "translation", t


def _stem(word: str) -> str:
    """Crude English stemming, enough to match 'adults' with 'adult' and 'opened' with 'opens'."""
    word = re.sub(r"'s$", "", word)
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word
