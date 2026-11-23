"""Adjudicator (plan §11): a second, narrower LLM call on the flags fusion is unsure about.

Only flags with decision "uncertain" (confidence in [uncertain, flag)) are asked about, all of an
item's in one call. The LLM sees the texts, the back-translation and each claim (category, the words,
why the checks raised it) and answers yes/no with a category, severity and an English reason. It
never gives spans: the flag keeps its own. Unlike the judge it is not blind; the rubric
(prompts/adjudicate.md) tells it the claims are often false and to answer "no" when unsure.

The answers are stored with the record (`adjudication`, keyed by the candidates' identity), so
re-fusing with other thresholds or calibration reuses them without new calls; fusion uses them only
when thresholds["adjudicate"] is true.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .clients import Clients, ModelError
from .judge import ALIASES, CATEGORIES, LANG_NAMES, SEVERITIES, _english_only
from .review import why

log = logging.getLogger("bodhan_eval.adjudicate")

RUBRIC = (Path(__file__).parent / "prompts" / "adjudicate.md").read_text(encoding="utf-8")


def ident(c: dict) -> list:
    """A candidate's identity, stable across re-fusing and subsets of the candidate list."""
    return [c["check"], c["category"], c["side"], list(c["span"])]


def adjudicate(record: dict, flags: list[dict], clients: Clients) -> list[dict]:
    """One answer per flag the LLM answered usably: {candidates, is_error, category, severity, reason}."""
    name = LANG_NAMES.get(record["lang"].split("-")[0], record["lang"])
    cands = record["candidates"]
    claims = []
    for i, f in enumerate(flags, 1):
        reasons = "; ".join(dict.fromkeys(f"{cands[j]['check']}: {why(cands[j])}" for j in f["candidates"]))
        where = "missing from the translation, in the English" if f["side"] == "source" else "in the translation"
        claims.append(f"C{i}: {f['category']}, about the words {where}: \"{f['text']}\"\n    raised because: {reasons}")
    user = (f"Translation language: {name}\n\nENGLISH SOURCE\n{record['source']}\n\nTRANSLATION ({name})\n"
            f"{record['translation']}\n\nMACHINE BACK-TRANSLATION INTO ENGLISH (often wrong)\n"
            f"{record.get('back_translation') or '(not available)'}\n\nCLAIMS\n" + "\n".join(claims))
    reply = clients.llm([{"role": "system", "content": RUBRIC}, {"role": "user", "content": user}])
    answers = reply.get("claims") if isinstance(reply, dict) else None
    if not isinstance(answers, list):
        raise ModelError("adjudicator reply has no 'claims' list")

    out = []
    for a in answers:
        if not isinstance(a, dict):
            continue
        k = str(a.get("claim", "")).strip().upper().lstrip("C")
        verdict = str(a.get("is_error", "")).strip().lower()
        if not k.isdigit() or not 1 <= int(k) <= len(flags) or verdict not in ("yes", "no"):
            continue
        f = flags[int(k) - 1]
        category = ALIASES.get(str(a.get("category", "")).strip().lower(), str(a.get("category", "")).strip().lower())
        severity = str(a.get("severity", "")).strip().lower()
        reason, stripped = _english_only(str(a.get("reason") or ""), record["translation"])
        if stripped:
            log.warning("adjudicator: removed %d non-English word(s) from a reason", stripped)
        out.append({"candidates": [ident(cands[j]) for j in f["candidates"]], "is_error": verdict == "yes",
                    "category": category if verdict == "yes" and category in CATEGORIES else None,
                    "severity": severity if verdict == "yes" and severity in SEVERITIES else None,
                    "reason": reason})
    if len(out) < len(flags):
        log.info("%s/%s: adjudicator answered %d of %d claims usably", record["id"], record["lang"], len(out), len(flags))
    return out
