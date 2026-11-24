"""judge.prompt: Judge prompt. ENGLISH ONLY: the only Indic text the model ever sees is the item's
judge.run: Run the judge on one item: prompt -> JSON -> validated errors with code-computed spans.
fusion: Fusion: turn judge errors into flags using the other checks as corroboration (brief 7.4).
consistency: Judge consistency (``--judge-rerun N``): re-judge N already-judged items and compare with
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .checks import Flag
from .config import LANGUAGES, PER_CATEGORY_PREFIX, TEXT_CATEGORIES, canonical_category, canonical_severity
from .infra import rate
from .text import quote_to_span

# -------------------- prompt
PROMPT_VERSION = "judge-v1"  # part of every cache key, via the prompt text itself


SYSTEM = """You are a strict translation quality reviewer for English to Indian-language machine translation.

Rules:
- You only READ the translation. Never write, rewrite, translate or correct text in any Indian language.
- Every "quote" must be copied character for character from the TRANSLATION. Do not paraphrase, fix or transliterate it.
- Explanations are in English, at most 25 words.
- First decide the verdict: "ok" if the translation is acceptable, "errors" if it has at least one error from the list below. Then list the errors.
- Do not report style preferences or acceptable variants (spelling, word order, formal or informal register, digits written as numerals or words). Report only real errors.

Error categories:
- omission: source content is missing from the translation. Quote the translation words right before the gap; put the missing English words in "source_quote".
- addition: the translation contains content that is not in the source.
- mistranslation: a word, phrase or sentence has the wrong meaning (including reversed meaning).
- number_unit: a number, date, unit or quantity differs from the source.
- term_entity: a name or technical term is wrong, or rendered inconsistently.
- format: Markdown, table, code or LaTeX structure is broken, lost or altered.
- wrong_language_script: wrong language or script, or text left untranslated in English.

Severities:
- critical: blocks understanding or reverses the meaning.
- major: meaning is still understandable but the error disrupts it.
- minor: technically wrong but harmless.

Hints from automatic checkers may be included. They may be noisy; verify them against the text yourself.

Answer with ONE JSON object and nothing else:
{"verdict": "ok" | "errors",
 "errors": [{"category": "<one category above>", "severity": "minor" | "major" | "critical",
             "quote": "<exact copy from the translation>", "source_quote": "<English source words, optional>",
             "explanation": "<English, at most 25 words>", "confidence": <number 0 to 1>}]}
Use "errors": [] when the verdict is "ok"."""


REPAIR = (
    "Your previous answer was not valid. Reply again with ONE JSON object exactly in the schema from the "
    'instructions: a "verdict" of "ok" or "errors", and an "errors" list. No other text.'
)


def build_user(source: str, translation: str, lang: str, hints: list[str]) -> str:
    """The item message. Strings are JSON-encoded so quoting and newlines are unambiguous."""
    name = LANGUAGES[lang].name if lang in LANGUAGES else lang
    parts = [
        f"Target language: {name} ({lang})",
        f"SOURCE (English): {json.dumps(source, ensure_ascii=False)}",
        f"TRANSLATION: {json.dumps(translation, ensure_ascii=False)}",
    ]
    if hints:
        parts.append("Automatic checker hints (may be noisy):")
        parts += [f"- {h}" for h in hints]
    return "\n".join(parts)


# -------------------- run
MAX_EXPLANATION_WORDS = 25


NOT_FOUND_CONFIDENCE_FACTOR = 0.5


DEFAULT_CONFIDENCE = 0.5


class InvalidJudgment(ValueError):
    pass


@dataclass
class JudgeOutcome:
    status: str  # "ran" | "skipped" | "error"
    reason: str = ""
    verdict: str | None = None  # "ok" | "errors"
    errors: list[dict] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # why individual errors were discarded
    repaired: bool = False
    cached: bool = False
    tokens: int = 0

    def to_dict(self) -> dict:
        d = {"status": self.status, "reason": self.reason, "verdict": self.verdict, "errors": self.errors,
             "dropped": self.dropped, "repaired": self.repaired, "tokens": self.tokens}
        return {k: v for k, v in d.items() if v not in (None, "", [], False)} | {"status": self.status}


def should_skip(flags: list[dict]) -> str | None:
    """Skip the judge when a strong deterministic text flag (incl. API truncation) already
    fails the item. The skip and its reason are still recorded on the item."""
    for f in sorted(flags, key=lambda f: f["check_id"]):
        if not f["needs_corroboration"] and f["category"] in TEXT_CATEGORIES and f["check_id"] != "llm_judge":
            return f"tier1_unambiguous:{f['check_id']}"
    return None


def _confidence(raw) -> float:
    try:
        c = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_CONFIDENCE
    return min(1.0, max(0.0, c))


def _short(text, limit: int = MAX_EXPLANATION_WORDS) -> str:
    words = str(text or "").split()
    return " ".join(words[:limit])


def parse_judgment(data: dict | None, translation: str) -> tuple[str, list[dict], list[str]]:
    """Validate a judge answer. Returns (verdict, errors, dropped reasons). Raises
    InvalidJudgment when the answer as a whole is unusable (triggers the repair retry)."""
    if not isinstance(data, dict):
        raise InvalidJudgment("not a JSON object")
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in ("ok", "errors"):
        raise InvalidJudgment(f"bad verdict {data.get('verdict')!r}")
    raw_errors = data.get("errors", [])
    if raw_errors is None:
        raw_errors = []
    if not isinstance(raw_errors, list):
        raise InvalidJudgment("'errors' is not a list")
    errors, dropped = [], []
    if verdict == "ok":  # the binary verdict comes first and wins; listed errors are ignored
        if raw_errors:
            dropped.append(f"ignored_{len(raw_errors)}_errors_on_ok_verdict")
        return verdict, errors, dropped
    for e in raw_errors:
        if not isinstance(e, dict):
            dropped.append("not_an_object")
            continue
        cat = canonical_category(str(e.get("category", "")))
        if cat not in TEXT_CATEGORIES:
            dropped.append(f"category:{cat}")
            continue
        quote = e.get("quote") if isinstance(e.get("quote"), str) else ""
        conf = _confidence(e.get("confidence"))
        m = quote_to_span(translation, quote) if quote else None
        err = {
            "category": cat,
            "severity": canonical_severity(e.get("severity")) or "major",
            # Always slice the original translation when matched so no LLM-written Indic text (even normalized) is stored.
            "quote": translation[m.span[0]:m.span[1]] if m else None,
            "quote_len": len(quote),
            "explanation": _short(e.get("explanation")),
            "confidence": conf if m else conf * NOT_FOUND_CONFIDENCE_FACTOR,
            "span": list(m.span) if m else None,
            "quote_found": m is not None,
            "match": m.method if m else None,
        }
        if isinstance(e.get("source_quote"), str) and e["source_quote"].strip():
            err["source_quote"] = e["source_quote"].strip()
        errors.append(err)
    errors.sort(key=lambda x: (x["span"] is None, x["span"] or [0, 0], x["category"], x["quote"] or "", x["quote_len"]))
    return verdict, errors, dropped


async def judge_item(llm, *, source: str, translation: str, lang: str, hints: list[str],
                     variant: str = "", bypass_cache: bool = False) -> JudgeOutcome:
    user = build_user(source, translation, lang, hints)
    res = await llm.complete_json(SYSTEM, user, variant=variant, bypass_cache=bypass_cache)
    tokens = res.prompt_tokens + res.completion_tokens
    try:
        verdict, errors, dropped = parse_judgment(res.data, translation)
        return JudgeOutcome("ran", verdict=verdict, errors=errors, dropped=dropped, cached=res.cached, tokens=tokens)
    except InvalidJudgment as first:
        repair_user = f"{user}\n\nPrevious answer:\n{res.raw[:1500]}\n\n{REPAIR}"
        res2 = await llm.complete_json(SYSTEM, repair_user, variant=variant + "|repair", bypass_cache=bypass_cache)
        tokens += res2.prompt_tokens + res2.completion_tokens
        try:
            verdict, errors, dropped = parse_judgment(res2.data, translation)
        except InvalidJudgment as second:
            return JudgeOutcome("error", reason=f"invalid_json_after_repair: {first}; {second}", tokens=tokens)
        return JudgeOutcome("ran", verdict=verdict, errors=errors, dropped=dropped, repaired=True, cached=res2.cached, tokens=tokens)


# -------------------- fusion
JUDGE_CHECK_ID = "llm_judge"


COMPATIBLE: dict[str, frozenset[str]] = {
    "omission": frozenset({"omission"}),
    "addition": frozenset({"addition", "mistranslation"}),
    "mistranslation": frozenset({"mistranslation", "addition", "omission", "term_entity"}),
    "number_unit": frozenset({"number_unit"}),
    "term_entity": frozenset({"term_entity", "mistranslation", "omission"}),
    "format": frozenset({"format"}),
    "wrong_language_script": frozenset({"wrong_language_script"}),
}


def min_confidence(category: str, thresholds: dict) -> float:
    return float(thresholds.get(PER_CATEGORY_PREFIX + category, thresholds["judge_min_confidence"]))


def corroborators(error: dict, other_flags: list[dict]) -> list[str]:
    ok = COMPATIBLE.get(error["category"], frozenset())
    return sorted({f["check_id"] for f in other_flags
                   if f["check_id"] != JUDGE_CHECK_ID and f["unit"] == "chars" and f["category"] in ok})


def fuse(other_flags: list[dict], judge_errors: list[dict], thresholds: dict) -> list[dict]:
    """Judge flags (as dicts) for one item. Errors below the confidence cutoff are dropped."""
    out = []
    for e in judge_errors:
        if e["confidence"] < min_confidence(e["category"], thresholds):
            continue
        corr = corroborators(e, other_flags)
        if not e["quote_found"]:
            review = "quote_not_found"
        elif not corr:
            review = "judge_only"
        else:
            review = None
        ev = {"quote": e["quote"], "quote_found": e["quote_found"], "match": e["match"], "corroborated_by": corr}
        if not e["quote_found"]:  # the LLM's text is not in the translation: keep only its length
            ev["quote_len"] = e.get("quote_len", 0)
        if review:
            ev["review_queue"] = review
        if e.get("source_quote"):
            ev["source_quote"] = e["source_quote"]
        span = tuple(e["span"]) if e["span"] else None
        f = Flag(JUDGE_CHECK_ID, e["category"], e["severity"], span, "chars", e["explanation"] or "LLM judge error.",
                 ev, e["confidence"], needs_corroboration=review is not None)
        out.append(f.to_dict())
    return out


def strong_flags(item: dict, thresholds: dict) -> list[dict]:
    """The item's counted flags if fusion ran with ``thresholds`` (used by calibration)."""
    others = [f for f in item.get("flags", []) if f["check_id"] != JUDGE_CHECK_ID]
    judge = item.get("judge") or {}
    fused = fuse(others, judge.get("errors", []), thresholds)
    return [f for f in others + fused if not f["needs_corroboration"]]


def weak_flags(item: dict, thresholds: dict) -> list[dict]:
    """The item's weak signals and review-queue flags at ``thresholds`` (reported, never counted)."""
    others = [f for f in item.get("flags", []) if f["check_id"] != JUDGE_CHECK_ID]
    judge = item.get("judge") or {}
    fused = fuse(others, judge.get("errors", []), thresholds)
    return [f for f in others + fused if f["needs_corroboration"]]


# -------------------- consistency
RERUN_VARIANT = "consistency-1"


async def rerun_consistency(llm, jobs: list[dict], langs: list[str]) -> dict:
    """``jobs``: dicts with id, lang, source, translation, hints and the first ``outcome`` (JudgeOutcome)."""
    per_lang = {lg: {"verdict": 0, "categories": 0, "n": 0, "failed": 0} for lg in sorted(langs)}
    tokens = 0
    second_errors: dict[str, list[dict]] = {}  # used after fusion by counted_flag_agreement
    for job in jobs:
        b = per_lang.setdefault(job["lang"], {"verdict": 0, "categories": 0, "n": 0, "failed": 0})
        try:
            second = await judge_item(llm, source=job["source"], translation=job["translation"], lang=job["lang"],
                                      hints=job["hints"], variant=RERUN_VARIANT)
        except Exception:  # a failed re-judge is counted, never fatal
            b["failed"] += 1
            continue
        if second.status != "ran":
            b["failed"] += 1
            continue
        first = job["outcome"]
        second_errors[job["id"]] = second.errors
        tokens += second.tokens
        b["n"] += 1
        b["verdict"] += first.verdict == second.verdict
        b["categories"] += {e["category"] for e in first.errors} == {e["category"] for e in second.errors}
    total = {k: sum(b[k] for b in per_lang.values()) for k in ("verdict", "categories", "n", "failed")}

    def fmt(b: dict) -> dict:
        return {"same_verdict": rate(b["verdict"], b["n"]), "same_categories": rate(b["categories"], b["n"]), "failed": b["failed"]}

    return {"items_requested": len(jobs), "tokens": tokens, "overall": fmt(total), "by_lang": {lg: fmt(b) for lg, b in sorted(per_lang.items())},
            "_second_errors": second_errors}


def _counted(flags) -> list[tuple]:
    """Judge flags that count (not sent to the review queue), as comparable (category, span) pairs."""
    return sorted((f["category"], tuple(f["span"] or ())) for f in flags if not f["needs_corroboration"])


def counted_flag_agreement(consistency: dict, results: list[dict], thresholds: dict) -> None:
    """After fusion: does the re-judge produce the same COUNTED judge flags as the first judge?
    Verdict flips that fusion sends to the review queue do not change the report, so this is
    the consistency of what the report actually counts. Adds ``same_counted_flags`` in place."""
    second_errors = consistency.pop("_second_errors", {})
    by_id = {r["id"]: r for r in results}
    per_lang: dict[str, list[int]] = {}
    for item_id, errors in sorted(second_errors.items()):
        rec = by_id.get(item_id)
        if rec is None or rec.get("status") == "error":
            continue
        others = [f for f in rec["flags"] if f["check_id"] != JUDGE_CHECK_ID]
        first = _counted(f for f in rec["flags"] if f["check_id"] == JUDGE_CHECK_ID)
        second = _counted(fuse(others, errors, thresholds))
        b = per_lang.setdefault(rec["lang"], [0, 0])
        b[0] += first == second
        b[1] += 1
    consistency["overall"]["same_counted_flags"] = rate(sum(b[0] for b in per_lang.values()), sum(b[1] for b in per_lang.values()))
    for lg, b in per_lang.items():
        consistency["by_lang"].setdefault(lg, {})["same_counted_flags"] = rate(b[0], b[1])
