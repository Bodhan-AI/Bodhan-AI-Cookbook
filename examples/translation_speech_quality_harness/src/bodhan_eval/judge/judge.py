"""LLM judge and verifier.

Design rules enforced in code, not just in the prompt:
* The LLM never writes Indian-language text. The translation is shown as numbered tokens and the
  judge answers with token numbers; any Indic-script characters in its output are stripped and the
  item is noted. So spans are exact and nothing the LLM writes in an Indian language can reach a report.
* Temperature 0, and every call is cached by its exact input, so reruns are free and identical.
* `sample` > 0 requests an independent repeat, used to measure judge consistency.
"""
from __future__ import annotations

import json
import logging
from importlib import resources

import regex

from ..checks.base import Context, Signal
from ..clients import LLMClient
from ..taxonomy import SEVERITIES, TRANSLATION_CATEGORIES, canonical_category
from ..text.normalize import tokenize

log = logging.getLogger(__name__)
JUDGE_VERSION = "judge-v2"
RUBRIC = resources.files(__package__).joinpath("rubric.md").read_text(encoding="utf-8")
INDIC = regex.compile(r"[^\p{Script=Latin}\p{Script=Common}\p{Script=Inherited}\s]")


def _json(text: str) -> dict | None:
    text = text.strip()
    text = regex.sub(r"^```(?:json)?|```$", "", text, flags=regex.M).strip()
    m = regex.search(r"\{.*\}", text, regex.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def sanitize(s: str) -> tuple[str, bool]:
    """Strip any non-Latin letters from LLM-written text (the LLM must never write Indic text)."""
    cleaned = regex.sub(r"(?:" + INDIC.pattern + r")(?:[\s\p{M}]*(?:" + INDIC.pattern + r"))*", "[…]", s or "")
    return regex.sub(r"\s{2,}", " ", cleaned).strip(), cleaned != (s or "")


def numbered(tokens) -> str:
    return " ".join(f"[{i}]{t}" for i, t in enumerate(tokens))


def translation_parts(ctx: Context, tokens) -> list[str]:
    """The verbatim translation (for judging numbers, symbols and formatting) plus the same text as
    numbered word tokens (for pointing). The token view drops punctuation and symbols, so judging from it
    alone made the judge report '=' or '%' or table pipes as missing when they were present."""
    tr_tokens = [ctx.translation[t.start:t.end] for t in tokens]
    return [f"SOURCE (English):\n{ctx.item.source}",
            f"TRANSLATION ({ctx.lang.name}), verbatim:\n{ctx.translation}",
            "The same translation as numbered word tokens (punctuation, symbols and markup omitted; use ONLY to give token numbers):\n"
            + numbered(tr_tokens)]


class Judge:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def _messages(self, ctx: Context, tokens) -> list[dict]:
        parts = translation_parts(ctx, tokens)
        bt = ctx.artifacts.get("backtranslation")
        if bt:
            parts.append(f"BACK-TRANSLATION (machine, may be wrong):\n{bt}")
        if ctx.item.reference:
            parts.append(f"REFERENCE TRANSLATION (human, for comparison):\n{ctx.item.reference}")
        return [{"role": "system", "content": RUBRIC}, {"role": "user", "content": "\n\n".join(parts)}]

    async def judge(self, ctx: Context, sample: int = 0) -> tuple[list[Signal], dict]:
        tokens = tokenize(ctx.translation)
        msgs = self._messages(ctx, tokens)
        raw = await self.llm.chat(msgs, max_tokens=1800, sample=sample)
        data = _json(raw)
        if data is None:  # one repair attempt
            raw = await self.llm.chat(msgs + [{"role": "assistant", "content": raw[:2000]},
                                              {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object."}],
                                      max_tokens=1500, sample=sample)
            data = _json(raw)
        if data is None or not isinstance(data.get("errors", None), list):
            return [], {"status": "unparseable"}
        signals, sanitized = [], False
        for e in data["errors"][:12]:
            if not isinstance(e, dict):
                continue
            cat = canonical_category(str(e.get("category", "")))
            if cat not in TRANSLATION_CATEGORIES:
                continue
            sev = str(e.get("severity", "major")).lower()
            sev = sev if sev in SEVERITIES else "major"
            try:
                conf = float(e.get("confidence", 0.7))
            except (TypeError, ValueError):
                conf = 0.7
            conf = min(max(conf, 0.0), 1.0)
            span = None
            tk = e.get("tokens")
            if isinstance(tk, list) and len(tk) == 2 and all(isinstance(v, int) for v in tk) and tokens:
                a, b = sorted(tk)
                a, b = max(0, min(a, len(tokens) - 1)), max(0, min(b, len(tokens) - 1))
                span = (tokens[a].start, tokens[b].end)
            quote, s1 = sanitize(str(e.get("source_quote", "")))
            expl, s2 = sanitize(str(e.get("explanation", "")))
            sanitized |= s1 or s2
            src_span = None
            if quote:
                i = ctx.item.source.lower().find(quote.lower())
                if i >= 0:
                    src_span = (i, i + len(quote))
            signals.append(Signal("judge", cat, sev, conf, expl or f"Judge reports a {cat} error.", span, src_span,
                                  {"source_quote": quote, "judge_sample": sample}))
        meta = {"status": "ok", "n_errors": len(signals)}
        if sanitized:
            meta["sanitized"] = True
            log.warning("judge output contained non-Latin text; stripped", extra={"event": "judge.sanitized", "item": ctx.item.id})
        return signals, meta

    async def verify(self, ctx: Context, sig: Signal) -> tuple[bool | None, float, str]:
        """Focused second look at one suspected error. Returns (confirmed, confidence, reason)."""
        tokens = tokenize(ctx.translation)
        where = ""
        if sig.span:
            idx = [i for i, t in enumerate(tokens) if t.start < sig.span[1] and sig.span[0] < t.end]
            if idx:
                where = f"The suspected location is tokens [{idx[0]}, {idx[-1]}]."
        if sig.source_span:
            where += f' It concerns this part of the source: "{ctx.item.source[sig.source_span[0]:sig.source_span[1]]}".'
        q = (f"An automatic check suspects a '{sig.category}' error: {sig.message} {where}\n"
             "Look only at this suspicion. Is it a real error in the TRANSLATION (not just a paraphrase, and not a mistake of the "
             "automatic check or of the back-translation)? Reply with JSON only: "
             '{"real_error": true|false, "confidence": 0.0-1.0, "reason": "one short English sentence"}')
        parts = translation_parts(ctx, tokens)
        if ctx.artifacts.get("backtranslation"):
            parts.append(f"BACK-TRANSLATION (machine, may be wrong):\n{ctx.artifacts['backtranslation']}")
        msgs = [{"role": "system", "content": RUBRIC}, {"role": "user", "content": "\n\n".join(parts) + "\n\n" + q}]
        data = _json(await self.llm.chat(msgs, max_tokens=600))
        if not data or "real_error" not in data:
            return None, 0.0, "verifier reply unparseable"
        try:
            conf = min(max(float(data.get("confidence", 0.6)), 0.0), 1.0)
        except (TypeError, ValueError):
            conf = 0.6
        reason, _ = sanitize(str(data.get("reason", "")))
        return bool(data["real_error"]), conf, reason
