"""LLM judge: reads source, numbered translation tokens, back-translation and check hints;
returns errors as token index ranges, which are mapped back to exact character spans.

The model never writes Indian-language text: it points at translation tokens by index and
quotes only the English source. Anything else in its reply is dropped during validation.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from functools import lru_cache
from importlib import resources
from itertools import combinations
from typing import Any, Protocol

from rapidfuzz import fuzz

from bodhan_eval.categories import SEVERITIES, TRANSLATION_CATEGORIES
from bodhan_eval.checks.base import CheckResult, Flag, map_source_span
from bodhan_eval.languages import Language
from bodhan_eval.normalize import Token, script_of, spans_overlap, tokenize

NAME = "judge"
VERDICTS = ("clean", "minor", "major")
# Prior confidence per severity before calibration: the judge is more reliable on clear,
# severe errors than on minor ones.
_CONF = {"minor": 0.45, "major": 0.6, "critical": 0.7}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(TRANSLATION_CATEGORIES)},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "token_start": {"type": "integer"},
                    "token_end": {"type": "integer"},
                    "source_quote": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": [
                    "category",
                    "severity",
                    "token_start",
                    "token_end",
                    "source_quote",
                    "explanation",
                ],
                "additionalProperties": False,
            },
        },
        "verdict": {"type": "string", "enum": list(VERDICTS)},
    },
    "required": ["errors", "verdict"],
    "additionalProperties": False,
}


class JudgeLLM(Protocol):
    async def chat_json(
        self, messages, *, sample: int = ..., max_tokens: int = ..., tag: str = ..., schema=...
    ) -> tuple[Any, bool]: ...


@lru_cache(maxsize=1)
def rubric() -> str:
    return resources.files("bodhan_eval").joinpath("judge/rubric.md").read_text("utf-8")


def number_tokens(translation: str) -> tuple[list[Token], str]:
    """Tokens of the translation and their rendering: each word as `[i]word`, with the
    punctuation and symbols between words kept verbatim (unnumbered) and line breaks kept.
    Hiding them would make the judge report missing "°", "%" or "।" that are really there."""
    toks = tokenize(translation, loose=False)

    def gap(text: str) -> str:
        return "\n".join(re.sub(r"[ \t]+", " ", line) for line in text.split("\n"))

    parts: list[str] = []
    prev_end = 0
    for i, t in enumerate(toks):
        parts.append(gap(translation[prev_end : t.start]))
        parts.append(f"[{i}]{t.text}")
        prev_end = t.end
    parts.append(gap(translation[prev_end:]))
    return toks, "".join(parts).strip()


def build_messages(
    source: str,
    numbered: str,
    lang: Language,
    back_translation: str | None,
    hints: list[Flag] | tuple[Flag, ...],
) -> list[dict[str, str]]:
    blocks = [
        f"TARGET LANGUAGE: {lang.name} ({lang.code})",
        f"SOURCE:\n{source}",
        f"TRANSLATION (numbered tokens):\n{numbered}",
    ]
    if back_translation:
        blocks.append(f"BACK-TRANSLATION:\n{back_translation}")
    if hints:
        lines = [
            f"- {h.category} ({h.check}, confidence {h.confidence:.2f}): {h.evidence}"
            for h in hints
        ]
        blocks.append("AUTOMATIC CHECK HINTS:\n" + "\n".join(lines))
    return [
        {"role": "system", "content": rubric()},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def _is_english(text: str) -> bool:
    return all(script_of(ch) in (None, "Latn") for ch in text)


def _strip_non_latin(text: str) -> str:
    """Replace runs of non-Latin letters (quoted Indic words) with '…', keeping the English."""
    out, in_run = [], False
    for ch in text:
        if script_of(ch) not in (None, "Latn") or (in_run and unicodedata.category(ch)[0] == "M"):
            if not in_run:
                out.append("…")
            in_run = True
        else:
            out.append(ch)
            in_run = False
    return re.sub(r"[“\"'‘’]\s*…\s*[”\"'‘’]", "…", "".join(out)).strip()


def parse_judgement(
    raw: Any, source: str, translation: str, tokens: list[Token]
) -> tuple[list[Flag], dict[str, Any]]:
    """Validate the model's JSON and turn each error into a Flag with a character span.

    Drops: unknown categories, out-of-range indices, non-English quotes. Never raises on a
    malformed reply; problems are counted in the returned info."""
    info: dict[str, Any] = {"dropped": [], "verdict": None}
    if not isinstance(raw, dict):
        info["dropped"].append("reply is not a JSON object")
        return [], info
    verdict = raw.get("verdict")
    info["verdict"] = verdict if verdict in VERDICTS else None
    flags: list[Flag] = []
    n = len(tokens)
    for k, e in enumerate(raw.get("errors") or []):
        if not isinstance(e, dict):
            info["dropped"].append(f"error {k}: not an object")
            continue
        cat, sev = e.get("category"), e.get("severity")
        if cat not in TRANSLATION_CATEGORIES:
            info["dropped"].append(f"error {k}: unknown category {cat!r}")
            continue
        if sev not in SEVERITIES:
            sev = "major"
        try:
            a, b = int(e.get("token_start", -1)), int(e.get("token_end", -1))
        except (TypeError, ValueError):
            info["dropped"].append(f"error {k}: non-integer token indices")
            continue
        if a > b:
            a, b = b, a
        if b >= n or a < -1:
            info["dropped"].append(f"error {k}: token range {a}-{b} outside 0-{n - 1}")
            continue
        quote = str(e.get("source_quote") or "").strip()
        explanation = str(e.get("explanation") or "").strip()
        if quote and not _is_english(quote):
            quote = ""  # the judge must never write Indic text; discard it
        if not _is_english(explanation):
            explanation = _strip_non_latin(explanation)

        src_span = None
        if quote:
            al = fuzz.partial_ratio_alignment(quote.lower(), source.lower())
            if al is not None and al.score >= 85:
                src_span = (al.dest_start, al.dest_end)

        if a == -1:  # omission at the very start, or no location given
            if src_span:
                span, _ = map_source_span(source, translation, src_span)
            else:
                span = (0, tokens[0].end) if tokens else None
        else:
            span = (tokens[a].start, tokens[b].end)

        flags.append(
            Flag(
                cat,
                sev,
                NAME,
                _CONF[sev],
                span,
                explanation + (f" (source: “{quote}”)" if quote else ""),
                source_span=src_span,
                details={"token_start": a, "token_end": b, "insertion_point": cat == "omission"},
            )
        )
    return flags, info


async def judge_item(
    llm: JudgeLLM,
    source: str,
    translation: str,
    lang: Language,
    *,
    back_translation: str | None = None,
    hints: list[Flag] | tuple[Flag, ...] = (),
    sample: int = 0,
) -> CheckResult:
    if not translation.strip():
        return CheckResult.skipped(NAME, "empty translation")
    tokens, numbered = number_tokens(translation)
    messages = build_messages(source, numbered, lang, back_translation, hints)
    raw, cached = await llm.chat_json(messages, sample=sample, tag=NAME, schema=SCHEMA)
    flags, info = parse_judgement(raw, source, translation, tokens)
    return CheckResult(
        NAME,
        flags=flags,
        signals={
            "verdict": info["verdict"],
            "errors_returned": len((raw or {}).get("errors") or []) if isinstance(raw, dict) else 0,
            "dropped": info["dropped"],
            "cached": cached,
            "sample": sample,
        },
    )


# ---------------------------------------------------------------------------
# Consistency
# ---------------------------------------------------------------------------


def consistency_sample(item_ids: list[str], k: int, seed: int = 0) -> list[str]:
    """Deterministic sample of k item ids (stable across runs and machines)."""

    def h(i: str) -> str:
        return hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()

    return sorted(item_ids, key=h)[: max(0, k)]


def _matches(a: Flag, b: Flag) -> bool:
    if a.category != b.category:
        return False
    if a.span is None or b.span is None:
        return a.span is None and b.span is None
    return spans_overlap(a.span, b.span) or a.span == b.span


def agreement(runs: list[CheckResult]) -> dict[str, Any]:
    """Agreement between repeated judgements of the same item.

    verdict: share of run pairs with the same verdict.
    flags:   per run pair, matched flags (same category, overlapping span) over the union
             (a Jaccard index); 1.0 when both runs found nothing.
    """
    runs = [r for r in runs if r.ran]
    pairs = list(combinations(runs, 2))
    if not pairs:
        return {"runs": len(runs), "verdict": None, "flags": None}
    v_same, jac = 0, []
    for x, y in pairs:
        v_same += x.signals.get("verdict") == y.signals.get("verdict")
        used: set[int] = set()
        matched = 0
        for f in x.flags:
            for j, g in enumerate(y.flags):
                if j not in used and _matches(f, g):
                    used.add(j)
                    matched += 1
                    break
        union = len(x.flags) + len(y.flags) - matched
        jac.append(1.0 if union == 0 else matched / union)
    return {
        "runs": len(runs),
        "verdict": round(v_same / len(pairs), 4),
        "flags": round(sum(jac) / len(jac), 4),
    }


async def judge_repeats(
    llm: JudgeLLM,
    source: str,
    translation: str,
    lang: Language,
    repeats: int,
    **kw,
) -> tuple[list[CheckResult], dict[str, Any]]:
    """Judge the same item `repeats` times (samples 1..repeats, so the main run's cached
    sample 0 is not reused) and report agreement."""
    runs = [
        await judge_item(llm, source, translation, lang, sample=s, **kw)
        for s in range(1, repeats + 1)
    ]
    return runs, agreement(runs)
