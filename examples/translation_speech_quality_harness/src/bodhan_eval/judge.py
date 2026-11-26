"""LLM judge. Reads Indian-language text but never writes it: the schema only allows an English
explanation and a *verbatim quote* copied from the source or translation. Quotes that cannot be
located verbatim are discarded as ungrounded, which is also how we localize the span."""
from __future__ import annotations

import json
import re

from .align import overlap
from .clients import Services
from .normalize import script_of

CATEGORIES = ["omission", "addition", "mistranslation", "number", "term", "format", "wrong_language"]
SEVERITIES = ["minor", "major", "critical"]

RUBRIC = """You are a strict translation quality auditor for English -> {lang_name} ({lang}) machine translation.
You may READ {lang_name}; you must NEVER write, rewrite, or correct {lang_name} text. Your only outputs are
category labels, severity labels, an English explanation, and a verbatim quote copied from the input.

Error categories (use exactly these names):
- omission: content of the source (a word, clause or sentence) is missing from the translation. Quote the SOURCE text that is missing.
- addition: the translation contains content that is not in the source. Quote the TRANSLATION text that was added.
- mistranslation: an ORDINARY word or phrase (not a name, acronym or technical term) has the wrong meaning. Quote the TRANSLATION words that are wrong.
- number: a digit, date, unit or quantity differs from the source. Quote the TRANSLATION number/unit.
- term: a NAME (person, place, organization, product), acronym, or technical/domain term is rendered wrongly: misspelled, mis-transliterated, replaced by a different term, or rendered inconsistently. Quote the TRANSLATION term.
- format: Markdown, table or LaTeX structure is broken or altered. Quote the TRANSLATION fragment.
- wrong_language: output is in the wrong language/script, or text was left untranslated. Quote the TRANSLATION fragment.

Severity: minor (meaning intact, small defect), major (meaning changed or information lost), critical (dangerous or unusable).

When both fit, prefer `term` if the affected words are a name, acronym or technical term, and `mistranslation` only for ordinary vocabulary.

Rules:
1. Judge ONLY against the source. Fluency, style and politeness are NOT errors. A fluent translation that changes the meaning IS an error.
2. Acceptable, not errors: transliteration of names, spelling numbers as digits or digits as words in the same value, native or ASCII digits, natural reordering, Indic punctuation.
3. The back-translation (if given) was produced by a noisy machine system. It can contain its own mistakes. Use it only as a hint; never report an error that the source and translation do not support.
4. Do not invent errors. If the translation is faithful, return an empty list.
5. "quote" must be copied character-for-character from the source (omission only) or from the translation (all other categories). Keep quotes short (a word to one sentence).
6. "explanation" must be English only.

Return ONLY JSON: {{"errors": [{{"category": "...", "severity": "...", "side": "source" | "translation", "quote": "...", "explanation": "..."}}]}}"""

LANG_NAMES = {"hi": "Hindi", "ta": "Tamil", "bn": "Bengali", "te": "Telugu", "mr": "Marathi", "gu": "Gujarati",
              "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "or": "Odia", "as": "Assamese", "ur": "Urdu", "ne": "Nepali"}


def build_messages(item: dict, translation: str, bt: str | None) -> list[dict]:
    lang = item["lang"]
    system = RUBRIC.format(lang=lang, lang_name=LANG_NAMES.get(lang, lang))
    parts = [f"SOURCE (English):\n{item['source']}", f"TRANSLATION ({lang}):\n{translation}"]
    if item.get("reference"):
        parts.append(f"REFERENCE TRANSLATION (human, may differ in wording):\n{item['reference']}")
    if bt:
        parts.append(f"NOISY BACK-TRANSLATION of the translation to English (hint only):\n{bt}")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


def parse_response(raw: str) -> list[dict] | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group())
    except ValueError:
        return None
    errs = data.get("errors")
    return errs if isinstance(errs, list) else None


def _english_only(s: str) -> str:
    """Enforce 'LLM never writes Indic text': drop any non-Latin letters from explanations."""
    out, in_run = [], False
    for c in str(s):
        if script_of(c) in (None, "Latn"):
            out.append(c)
            in_run = False
        elif not in_run:           # collapse each run of Indic text into one marker
            out.append("[text]")
            in_run = True
    return "".join(out)[:400]


def locate(quote: str, text: str) -> list[int] | None:
    if not quote:
        return None
    i = text.find(quote)
    if i < 0:
        q = " ".join(quote.split())
        flat = re.sub(r"\s+", " ", text)
        i2 = flat.find(q)
        if i2 < 0:
            return None
        # map back (rare path): approximate with first-word search in the original
        first = q.split(" ")[0]
        j = text.find(first)
        return None if j < 0 else [j, min(len(text), j + len(q))]
    return [i, i + len(quote)]


def judge_item(item: dict, translation: str, bt: str | None, svc: Services, runs: int = 3) -> dict:
    """Run the judge `runs` times (run 0 deterministic, others slightly warmer to expose drift),
    cluster identical findings, and report agreement."""
    msgs = build_messages(item, translation, bt)
    per_run: list[list[dict]] = []
    failures = ungrounded = 0
    for r in range(runs):
        raw = svc.chat(msgs, temperature=0.0 if r == 0 else 0.3, run=r)
        errs = parse_response(raw)
        if errs is None:
            failures += 1
            per_run.append([])
            continue
        run_f = []
        for e in errs:
            if not isinstance(e, dict) or e.get("category") not in CATEGORIES:
                continue
            side = e.get("side") if e.get("side") in ("source", "translation") else (
                "source" if e.get("category") == "omission" else "translation")
            text = item["source"] if side == "source" else translation
            span = locate(str(e.get("quote", "")), text)
            if span is None:
                ungrounded += 1
                continue
            run_f.append({
                "category": e["category"],
                "severity": e.get("severity") if e.get("severity") in SEVERITIES else "major",
                "side": side, "span": span, "quote": text[span[0]:span[1]],
                "explanation": _english_only(e.get("explanation", "")),
            })
        per_run.append(run_f)

    clusters: list[dict] = []
    for r, run_f in enumerate(per_run):
        for f in run_f:
            for c in clusters:
                if c["category"] == f["category"] and c["side"] == f["side"] and overlap(c["span"], f["span"]) > 0:
                    c["runs"].add(r)
                    if SEVERITIES.index(f["severity"]) > SEVERITIES.index(c["severity"]):
                        c["severity"] = f["severity"]
                    break
            else:
                clusters.append({**f, "runs": {r}})
    ok_runs = max(1, runs - failures)
    findings = [{**{k: v for k, v in c.items() if k != "runs"}, "frac": round(len(c["runs"]) / ok_runs, 4)}
                for c in clusters]
    verdicts = [bool(rf) for rf in per_run]
    cats = [tuple(sorted({f["category"] for f in rf})) for rf in per_run]
    return {
        "findings": findings,
        "runs": runs, "parse_failures": failures, "ungrounded_quotes": ungrounded,
        "verdict_consistent": len(set(verdicts)) == 1,
        "category_consistent": len(set(cats)) == 1,
    }
