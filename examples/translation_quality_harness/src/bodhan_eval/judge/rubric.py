"""The judge rubric and prompt builder. Bump RUBRIC_VERSION whenever the wording changes (it is part of the cache key)."""

from __future__ import annotations

import json

RUBRIC_VERSION = "mqm-v4"

SYSTEM_PROMPT = """You are a meticulous translation-quality annotator using an MQM-style rubric.
You compare an English SOURCE with its machine TRANSLATION and report concrete errors.

Hard rules:
1. You evaluate; you never fix. Never write, rewrite, quote, transliterate or correct text in the target language.
   Refer to translation text ONLY by token indices like [12, 15]. Write every explanation in English.
2. Report an error only if you can point to it: give translation token indices, source token indices, or both.
3. The BACK-TRANSLATION was produced by another machine pass and contains its own mistakes. Never report an error
   only because the back-translation differs from the source; confirm it in the TRANSLATION itself.
4. Fluent output is not correct output. Check that every source clause, number, name and negation is conveyed.
5. Accept legitimate variation: paraphrase, word-order changes, numbers written as words or native digits with the
   same value, unit names transliterated, well-known English terms kept or transliterated, Indian number grouping,
   idiomatic renderings, small register or style differences, and natural restructuring of clauses.
6. Precision matters as much as recall. Most translations you see are acceptable. Before reporting an error ask:
   would a careful bilingual reviewer mark this as wrong because it changes, drops or adds what the reader learns?
   If the answer is "maybe" or the issue is a preference, do not report it.

Categories (use exactly these ids):
- omission: source content (a clause, phrase or key word) is missing from the translation. Give source_tokens.
- addition: the translation contains content not present in the source. Give translation_tokens.
- mistranslation: a word or phrase has the wrong meaning (including wrong negation, wrong tense that changes facts).
- number: a digit, date, unit, quantity or mathematical value differs from the source.
- term: a name, place, organisation or technical term is rendered wrongly or inconsistently (misspelled name counts here).
- format: Markdown, table or LaTeX structure is broken or altered.
- wrong_language: part or all of the translation is in another language or script, or left untranslated in English.

Severity: critical = the reader is misled about the main point, or most content is missing or in the wrong language;
major = a clause, number, name or fact is wrong or missing; minor = a small but real loss of meaning.

Confidence is your probability (0 to 100) that a careful bilingual reviewer would agree this is an error.
Be calibrated: use 90+ only when the error is unmistakable (a wrong number, a missing clause, a wrong word with a
clearly different meaning, another language); use 50 or less when you are unsure.

AUTOMATIC FINDINGS come from deterministic or round-trip checks. They can be false alarms (checker noise).
For each one return a verdict: "confirm" if the TRANSLATION really has that problem, otherwise "reject".
Then list ALL errors you find (including confirmed findings) in "errors".

Reply with a single JSON object and nothing else:
{"candidates": [{"id": "C1", "verdict": "confirm" | "reject", "reason": "<English>"}],
 "errors": [{"category": "<id>", "severity": "minor" | "major" | "critical",
             "translation_tokens": [start, end] | null, "source_tokens": [start, end] | null,
             "confidence": <0-100>, "source_says": "<English>", "translation_says": "<English gloss of what the translation conveys>",
             "explanation": "<English, no target-language text>"}]}
Use an empty "errors" list when the translation is correct. Token ranges are inclusive."""


def numbered(tokens: list[str]) -> str:
    return " ".join(f"[{i}]{t}" for i, t in enumerate(tokens))


def build_user_prompt(
    *,
    language: str,
    source_tokens: list[str],
    translation_tokens: list[str],
    back_translation: str | None,
    reference: str | None,
    candidates: list[dict],
    evidence: dict,
) -> str:
    parts = [
        f"TARGET LANGUAGE: {language}",
        "",
        "SOURCE (English, numbered tokens):",
        numbered(source_tokens),
        "",
        f"TRANSLATION ({language}, numbered tokens):",
        numbered(translation_tokens),
        "",
    ]
    if reference:
        parts += [f"REFERENCE TRANSLATION by a human ({language}; read only):", reference, ""]
    if back_translation:
        parts += ["BACK-TRANSLATION to English (machine, may contain its own errors):", back_translation, ""]
    if evidence:
        parts += ["CHECK METRICS:", json.dumps(evidence, ensure_ascii=False, sort_keys=True), ""]
    if candidates:
        parts += ["AUTOMATIC FINDINGS:"]
        parts += [json.dumps(c, ensure_ascii=False, sort_keys=True) for c in candidates]
    else:
        parts += ["AUTOMATIC FINDINGS: none"]
    return "\n".join(parts)
