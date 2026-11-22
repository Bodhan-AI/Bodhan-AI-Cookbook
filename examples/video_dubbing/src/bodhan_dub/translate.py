"""Translate dub units into one target language, check them in English, and recover.

All Indian-language text comes from Indic-Translate. To check it without ever having
the LLM write Indian-language text, every translation is back-translated to English
(also by Indic-Translate). Then:

  * code checks hard facts: script, numbers, glossary terms in the back-translation,
  * the LLM judges meaning: source English vs back-translation, one call per language,
  * units that fail get alternative candidates (per-sentence, code-mixed), which are
    checked and judged the same way, and code picks the best one.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from . import textcheck
from .bodhan import BodhanClient, BodhanError
from .glossary import Term
from .languages import LANGUAGE_NAMES
from .llm import LLM, LLMError
from .repair import Unit

log = logging.getLogger(__name__)

# A native translation below this share of target-script letters is in the wrong language;
# code-mixed text legitimately carries many English words, so its floor is lower.
MIN_SCRIPT_SHARE = {"native": 0.6, "codemix": 0.2}
VERDICT_RANK = {"ok": 2, "minor": 1, None: 1, "major": 0}
# Verdicts for 15 items fit comfortably in a ~2k-token reply, the default cap of some providers.
JUDGE_BATCH = 15

JUDGE_PROMPT = """You review machine translations of an educational video's narration by reading their back-translation into English.

For each item you get:
- "source": the English that was translated,
- "back": an automatic back-translation of the {language} translation into English. It is itself machine output and often paraphrases; wording differences are normal,
- "flags": findings of automatic checks (missing numbers, glossary terms absent from the back-translation). Confirm or dismiss each one.

Verdict per item:
- "ok": same meaning.
- "minor": small nuance or wording loss that would not mislead a learner.
- "major": meaning changed: a technical term rendered as a different concept, a clause or sentence missing or added, a number or label changed.

Do not call a paraphrase "major". Name the problem precisely in "issue" (empty if ok), in English.

Reply with JSON only: {{"verdicts": [{{"id": "u0003", "verdict": "major", "issue": "'interest rate' became 'interest amount'"}}]}}"""


@dataclass
class Candidate:
    kind: str  # "unit": whole unit; "sentences": sentence by sentence; "codemix": code-mixed
    text: str = ""
    back: str = ""
    truncated: bool = False
    script_share: float = 0.0
    missing_numbers: list[str] = field(default_factory=list)
    missing_terms: list[str] = field(default_factory=list)  # glossary terms absent from the back-translation
    missing_keep: list[str] = field(default_factory=list)  # keep=True terms changed in the translation itself
    hard_fail: list[str] = field(default_factory=list)  # reasons this candidate is unusable
    verdict: str | None = None
    issue: str = ""

    def rank(self) -> tuple:
        # Facts first, then the judge's reading of meaning, then the literal term match (back-translations
        # paraphrase terms, so the judge outranks it), and on a tie the plain native translation.
        return (not self.hard_fail, -len(self.missing_keep), -len(self.missing_numbers),
                VERDICT_RANK.get(self.verdict, 1), -len(self.missing_terms), -KIND_ORDER.index(self.kind))


KIND_ORDER = ["unit", "sentences", "codemix"]


@dataclass
class TranslatedUnit:
    id: str
    start: float
    end: float
    source_text: str
    translation: str = ""
    chosen: str = ""
    status: str = "ok"  # "ok", "flagged" (dubbed, with a logged concern) or "skipped"
    note: str = ""
    candidates: list[Candidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LanguageResult:
    lang: str
    status: str = "ok"  # "ok" or "failed"
    error: str = ""
    units: list[TranslatedUnit] = field(default_factory=list)
    judge_calls: int = 0
    judge_failed: int = 0
    # term -> {"english": [unit ids], "native": [unit ids]} after harmonising
    term_rendering: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    harmonised: list[str] = field(default_factory=list)  # units switched to keep a term consistent
    renderings_missing: dict[str, list[str]] = field(default_factory=dict)  # user rendering -> unit ids lacking it

    def summary(self) -> dict[str, Any]:
        by_status = {s: sum(u.status == s for u in self.units) for s in ("ok", "flagged", "skipped")}
        recovered = sum(len(u.candidates) > 1 and u.chosen != "unit" for u in self.units)
        mixed = sorted(t for t, r in self.term_rendering.items() if r["english"] and r["native"])
        return {"status": self.status, "error": self.error, "units": len(self.units), **by_status,
                "recovered_with_alternative": recovered, "judge_calls": self.judge_calls,
                "judge_failed": self.judge_failed,
                "terms": {"checked": len(self.term_rendering), "still_mixed": mixed,
                          "units_harmonised": self.harmonised, "user_renderings_missing": self.renderings_missing}}


class Translator:
    def __init__(self, client: BodhanClient, llm: LLM | None, glossary: list[Term], *,
                 code_mixed: bool = False, max_workers: int = 4):
        self.client = client
        self.llm = llm
        self.terms = [(t.term, t.kind) for t in glossary]
        # keep=True terms (brands, names) must appear unchanged in the translation itself.
        self.keep_terms = [(t.term, t.kind) for t in glossary if t.keep]
        # Terms whose rendering must match across units (labels are letters, not words to translate).
        self.consistency_terms = [(t.term, t.kind) for t in glossary if t.kind != "label" and not t.keep]
        self.user_renderings = {t.term: (t.kind, t.renderings) for t in glossary if t.renderings}
        self.code_mixed = code_mixed
        self.max_workers = max_workers

    # -- candidates -----------------------------------------------------------

    def _translate(self, text: str, lang: str, script: str) -> tuple[str, bool]:
        result = self.client.translate(text, lang, script=script)
        return result.text.strip(), result.truncated

    def make_candidate(self, unit: Unit, lang: str, kind: str) -> Candidate:
        script = "codemix" if kind == "codemix" or self.code_mixed else "native"
        cand = Candidate(kind=kind)
        try:
            if kind == "sentences":
                parts = [self._translate(s, lang, script) for s in textcheck.split_sentences(unit.text)]
                cand.text = " ".join(p for p, _ in parts)
                cand.truncated = any(t for _, t in parts)
            else:
                cand.text, cand.truncated = self._translate(unit.text, lang, script)
            cand.back = self.client.translate(cand.text, "en", bill_to=lang).text.strip() if cand.text else ""
        except BodhanError as exc:
            cand.hard_fail.append(f"translation call failed: {exc}")
            return cand
        self.check(cand, unit, lang, script)
        return cand

    def check(self, cand: Candidate, unit: Unit, lang: str, script: str) -> None:
        if not cand.text:
            cand.hard_fail.append("empty translation")
            return
        if cand.truncated:
            cand.hard_fail.append("translation truncated")
        cand.script_share = round(textcheck.script_share(cand.text, lang), 3)
        if cand.script_share < MIN_SCRIPT_SHARE[script]:
            cand.hard_fail.append(f"wrong script: only {cand.script_share:.0%} {LANGUAGE_NAMES[lang]} letters")
        cand.missing_numbers = textcheck.missing_numbers(unit.text, cand.text)
        cand.missing_terms = [t for t, k in textcheck.checkable_terms(self.terms, unit.text)
                              if not textcheck.term_in(t, k, cand.back)]
        cand.missing_keep = [t for t, k in textcheck.checkable_terms(self.keep_terms, unit.text)
                             if not textcheck.term_in(t, k, cand.text)]

    # -- judging ---------------------------------------------------------------

    def judge(self, pairs: list[tuple[Unit, Candidate]], lang: str, result: LanguageResult) -> None:
        """Judge candidates in batches small enough for any provider's reply limit."""
        for i in range(0, len(pairs), JUDGE_BATCH):
            self._judge_batch(pairs[i:i + JUDGE_BATCH], lang, result)

    def _judge_batch(self, pairs: list[tuple[Unit, Candidate]], lang: str, result: LanguageResult) -> None:
        """One LLM call for up to JUDGE_BATCH candidates. On any failure, verdicts stay None (neutral)."""
        if self.llm is None or not pairs:
            return
        keyed = {f"{unit.id}:{cand.kind}": cand for unit, cand in pairs}
        items = []
        for (unit, cand), key in zip(pairs, keyed, strict=True):
            flags = ([f"number {n} missing from translation" for n in cand.missing_numbers]
                     + [f"term '{t}' not found in back-translation" for t in cand.missing_terms]
                     + [f"term '{t}' should stay untranslated but was changed" for t in cand.missing_keep])
            items.append({"id": key, "source": unit.text, "back": cand.back, "flags": flags})
        result.judge_calls += 1
        try:
            reply = self.llm.complete_json(JUDGE_PROMPT.format(language=LANGUAGE_NAMES[lang]),
                                           json.dumps({"items": items}, ensure_ascii=False))
            verdicts = reply.get("verdicts") if isinstance(reply, dict) else None
            if not isinstance(verdicts, list):
                raise LLMError('judge reply must be {"verdicts": [...]}')
        except LLMError as exc:
            result.judge_failed += 1
            log.warning("judge failed; relying on deterministic checks", extra={"stage": "translate",
                                                                                 "lang": lang, "error": str(exc)})
            return
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            cand = keyed.get(str(v.get("id")))
            verdict = v.get("verdict")
            if cand is not None and verdict in ("ok", "minor", "major"):
                cand.verdict, cand.issue = verdict, str(v.get("issue") or "")[:300]

    # -- per language ------------------------------------------------------------

    def needs_recovery(self, cand: Candidate) -> bool:
        """Look for alternatives on hard facts, or when the judge reads a real change of meaning.

        A glossary term missing from the back-translation alone is weak evidence (back-translation
        paraphrases: "common arm" -> "common side"), so it only triggers recovery when no judge
        verdict exists to weigh it.
        """
        return bool(cand.hard_fail or cand.missing_numbers or cand.missing_keep or cand.verdict == "major"
                    or (cand.missing_terms and cand.verdict is None))

    def translate_language(self, units: list[Unit], lang: str) -> LanguageResult:
        result = LanguageResult(lang=lang)
        live = [u for u in units if not u.dropped]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            first = list(pool.map(lambda u: self.make_candidate(u, lang, "unit"), live))
        self.judge(list(zip(live, first, strict=True)), lang, result)

        candidates: dict[str, list[Candidate]] = {u.id: [c] for u, c in zip(live, first, strict=True)}
        retry = [(u, kind) for u, c in zip(live, first, strict=True) if self.needs_recovery(c)
                 for kind in self.alternative_kinds(u)]
        if retry:
            log.info("trying alternative translations", extra={"stage": "translate", "lang": lang,
                                                               "units": sorted({u.id for u, _ in retry})})
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                alts = list(pool.map(lambda job: self.make_candidate(job[0], lang, job[1]), retry))
            self.judge([(u, c) for (u, _), c in zip(retry, alts, strict=True)], lang, result)
            for (u, _), c in zip(retry, alts, strict=True):
                candidates[u.id].append(c)

        for unit in units:
            result.units.append(self.finalize(unit, candidates.get(unit.id, []), lang))
        self.harmonise_terms(units, result, lang)
        self.check_user_renderings(result, lang)
        return result

    # -- terminology consistency ------------------------------------------------------

    def rendering_of(self, term: str, kind: str, out: TranslatedUnit) -> str | None:
        """'english' if the term is kept in English in this unit's translation, else 'native'."""
        if out.status == "skipped" or not textcheck.term_in(term, kind, out.source_text):
            return None
        return "english" if textcheck.term_in(term, kind, out.translation) else "native"

    def tally(self, result: LanguageResult) -> dict[str, dict[str, list[str]]]:
        table: dict[str, dict[str, list[str]]] = {}
        for term, kind in self.consistency_terms:
            row: dict[str, list[str]] = {"english": [], "native": []}
            for out in result.units:
                if (r := self.rendering_of(term, kind, out)) is not None:
                    row[r].append(out.id)
            if row["english"] or row["native"]:
                table[term] = row
        return table

    def harmonise_terms(self, units: list[Unit], result: LanguageResult, lang: str) -> None:
        """Push each glossary term towards one rendering per language, without cascading.

        Units choose translations independently, so a term can be spoken in English in a unit
        recovered with a code-mixed translation and natively in its neighbours ("a term rendered two
        ways confuses viewers"). Switching a unit to code-mixed also keeps its *other* terms in
        English, so blanket switching cascades into heavy code-mixing (seen live: 8 of 23 Hindi units).
        So follow the majority, in one pass: where English is already the majority form of a term, the
        native units switch to their checked code-mixed translation. Where native is the majority,
        native is kept and the term is reported as mixed.
        """
        table = self.tally(result)
        if self.code_mixed:
            result.term_rendering = table
            return
        by_id = {u.id: u for u in units}
        outs = {o.id: o for o in result.units}
        to_switch = sorted({uid for row in table.values()
                            if row["english"] and row["native"] and len(row["english"]) >= len(row["native"])
                            for uid in row["native"]})
        fresh = [uid for uid in to_switch if not any(c.kind == "codemix" for c in outs[uid].candidates)]
        if fresh:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                made = list(pool.map(lambda uid: self.make_candidate(by_id[uid], lang, "codemix"), fresh))
            self.judge([(by_id[uid], c) for uid, c in zip(fresh, made, strict=True)], lang, result)
            for uid, cand in zip(fresh, made, strict=True):
                outs[uid].candidates.append(cand)
        for uid in to_switch:
            cand = next(c for c in outs[uid].candidates if c.kind == "codemix")
            if cand.hard_fail or cand.missing_numbers or cand.missing_keep or cand.verdict == "major":
                continue
            outs[uid].chosen, outs[uid].translation = "codemix", cand.text
            outs[uid].note = (outs[uid].note + "; " if outs[uid].note else "") + \
                "switched to code-mixed so a glossary term is rendered the same way as in most units"
            result.harmonised.append(uid)
            log.info("unit harmonised", extra={"stage": "translate", "lang": lang, "unit": uid})
        result.term_rendering = self.tally(result)

    def check_user_renderings(self, result: LanguageResult, lang: str) -> None:
        """Verify-only: report units whose translation lacks a rendering the user's glossary expects."""
        for term, (kind, renderings) in self.user_renderings.items():
            expected = renderings.get(lang)
            if expected is None and "*" in renderings and textcheck.script_share(renderings["*"], lang) > 0.5:
                expected = renderings["*"]
            if not expected:
                continue
            want = unicodedata.normalize("NFC", expected)
            missing = [o.id for o in result.units if o.status != "skipped" and textcheck.term_in(term, kind, o.source_text)
                       and want not in unicodedata.normalize("NFC", o.translation)]
            if missing:
                result.renderings_missing[term] = missing

    def alternative_kinds(self, unit: Unit) -> list[str]:
        kinds = []
        if len(textcheck.split_sentences(unit.text)) > 1:
            kinds.append("sentences")
        if not self.code_mixed:
            kinds.append("codemix")
        return kinds

    def finalize(self, unit: Unit, cands: list[Candidate], lang: str) -> TranslatedUnit:
        out = TranslatedUnit(id=unit.id, start=unit.start, end=unit.end, source_text=unit.text, candidates=cands)
        ctx = {"stage": "translate", "lang": lang, "unit": unit.id}
        if unit.dropped:
            out.status, out.note = "skipped", f"not narration: {unit.dropped}"
            return out
        best = max(cands, key=Candidate.rank)
        out.chosen, out.translation = best.kind, best.text
        if best.hard_fail:
            out.status, out.note = "skipped", "; ".join(best.hard_fail)
            out.translation = ""
        elif self.needs_recovery(best):
            problems = ([f"numbers missing: {', '.join(best.missing_numbers)}"] if best.missing_numbers else []) \
                + ([f"must stay untranslated: {', '.join(best.missing_keep)}"] if best.missing_keep else []) \
                + ([f"judge: {best.issue}"] if best.verdict == "major" else []) \
                + ([f"terms not confirmed: {', '.join(best.missing_terms)}"] if best.missing_terms else [])
            out.status, out.note = "flagged", "; ".join(problems)
        elif best.missing_terms:
            out.note = f"terms paraphrased in back-translation, judge: {best.verdict}: {', '.join(best.missing_terms)}"
        log.info("unit translated", extra={**ctx, "status": out.status, "chosen": out.chosen,
                                           "candidates": len(cands), "verdict": best.verdict, "note": out.note})
        return out
