"""Run the checks on every item and build one record per item for items.jsonl.

Order per item: back-translation check (§10, which also yields the back-translation), the LLM judge
(§11, blind to the other findings), then the deterministic checks (§9). Then dataset-level checks (length per language). A failing
check is recorded as an error for that item and never stops the run.

Then fusion (§13, fuse.py) turns each item's candidates into flags and a verdict, and the adjudicator (§11)
answers about the flags fusion is unsure of; their items are fused again with the answers.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from .adjudicate import adjudicate
from .backtrans import check_back_translation
from .checks import Candidate, check_entities, check_format, check_numbers, check_script, length_outliers, reference_scores
from .clients import BudgetExceeded, Clients, ModelError
from .dataset import Item
from .fuse import fuse, load_thresholds
from .judge import MAX_CHARS, judge
from .segment import speakable
from .speech import CHARS_PER_SECOND, check_speech, consistency, rate_candidate, spoken_length
from .langs import expected_scripts

log = logging.getLogger("bodhan_eval.pipeline")

WORKERS = 8


def run_pipeline(items: list[Item], clients: Clients, thresholds: dict | None = None, speech: bool = True,
                 audio_ready=None) -> list[dict]:
    """One record per item, in dataset order. Text checks for every item first; then, once
    `audio_ready()` returns (audio generated alongside the text checks), the speech checks."""
    thresholds = thresholds or load_thresholds()
    by_lang: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        by_lang[item.lang].append(item)
    expected = {lang: expected_scripts(lang, [i.translation for i in group if i.translation]) for lang, group in by_lang.items()}
    with ThreadPoolExecutor(WORKERS) as pool:
        records = list(pool.map(lambda i: score_item(i, clients, expected[i.lang]), items))
        if audio_ready is not None:
            audio_ready()
        list(pool.map(lambda r: score_speech(r, clients, speech), records))

    index = {(r["id"], r["lang"]): r for r in records}
    for lang, group in by_lang.items():
        pairs = {i.key: (i.source, i.translation) for i in group if i.translation}
        for key, cand in length_outliers(pairs).items():
            index[key]["candidates"].append(_record(cand, index[key]["_item"]))
        for i in group:
            if i.translation is not None:
                index[i.key]["checks"]["length"] = "ok" if len(pairs) >= 5 else "skipped: fewer than 5 items in this language"
        _speech_rate([index[i.key] for i in group])

    for r in records:
        item = r.pop("_item")
        r["candidates"].sort(key=lambda c: (c["side"], c["span"], c["category"], c["check"]))
        r["flags"], r["verdict"], r["mqm"] = fuse(r, thresholds)
        log.debug("%s/%s: %d candidates, %s", item.id, item.lang, len(r["candidates"]), r["verdict"])
    if thresholds.get("adjudicate"):
        unsure = [r for r in records if _unsure(r)]
        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(lambda r: _adjudicate(r, clients, thresholds), unsure))
    return records


def _adjudicate(r: dict, clients: Clients, thresholds: dict) -> None:
    if not clients.available("llm"):
        r["checks"]["adjudicator"] = "skipped: LLM not configured"
        return
    try:
        r["adjudication"] = adjudicate(r, _unsure(r), clients)
        r["checks"]["adjudicator"] = "ok"
    except ModelError as e:
        r["checks"]["adjudicator"] = f"error: {e}"
        return
    r["flags"], r["verdict"], r["mqm"] = fuse(r, thresholds)


def _unsure(r: dict) -> list[dict]:
    """Uncertain text flags: the adjudicator reads texts, not audio, so speech flags stay uncertain."""
    return [f for f in r["flags"] if f["decision"] == "uncertain" and not f["category"].startswith("speech_")]


def _speech_rate(records: list[dict]) -> None:
    """Audio far shorter or longer than its text needs, at this language's median speaking rate."""
    timed = [r for r in records if r.get("speech", {}).get("duration_s")]
    rates = [spoken_length(r["translation"]) / r["speech"]["duration_s"] for r in timed]
    rate = float(sorted(rates)[len(rates) // 2]) if len(rates) >= 5 else CHARS_PER_SECOND
    for r in timed:
        c = rate_candidate(r["translation"], r["speech"]["duration_s"], rate)
        if c is not None:
            r["candidates"].append(_record(c, r["_item"]))
        expected = spoken_length(r["translation"]) / rate
        consistency(r["candidates"], r["speech"]["duration_s"] / expected if expected else 1.0)
        r["checks"]["speech_rate"] = "ok" if len(rates) >= 5 else f"ok: default rate {CHARS_PER_SECOND} chars/s (< 5 clips)"


def score_item(item: Item, clients: Clients, expected: set[str] | None) -> dict:
    record: dict = {
        "id": item.id, "lang": item.lang, "content_type": item.content_type, "status": dict(item.status),
        # The texts spans point into; a generated translation exists nowhere else but the cache.
        "source": item.source, "translation": item.translation,
        "translation_origin": item.extra.get("translation_origin")
        or ("bodhan" if item.status.get("translation") == "generated" else None),
        "checks": {}, "candidates": [], "suppressed": [], "scores": {}, "back_translation": None,
        "warnings": list(item.warnings), "_item": item,
    }
    checks, candidates = record["checks"], record["candidates"]
    if item.translation is None:
        checks["translation"] = f"skipped: no translation ({item.status.get('translation', 'missing')})"
        return record
    source, translation = item.source, item.translation

    back = None
    if not clients.available("translate"):
        checks["back_translation"] = "skipped: BODHAN_TRANSLATE_KEY not set"
    else:
        try:
            bt = check_back_translation(source, translation, item.lang, clients,
                                        generated=item.status.get("translation") == "generated")
            # Both back-translations, when two were made: the whole-item one sometimes drops a table or a
            # sentence the per-sentence one keeps, and the number and name checks should see all evidence.
            back = "\n".join(bt.back_texts) or None
            candidates.extend(_record(c, item) for c in bt.candidates)
            record["suppressed"] = bt.suppressed
            record["control"] = bt.control
            checks["back_translation"] = "ok"
        except BudgetExceeded:
            checks["back_translation"] = "skipped: budget"
        except ModelError as e:
            checks["back_translation"] = f"error: {e}"
    record["back_translation"] = back

    # The judge goes before the code checks and never sees their findings (blind, so it isn't anchored).
    if not clients.available("llm"):
        checks["judge"] = "skipped: LLM not configured"
    elif len(source) + len(translation) > MAX_CHARS:
        checks["judge"] = f"skipped: longer than {MAX_CHARS} characters"
    else:
        try:
            found, record["judge"] = judge(source, translation, back, item.lang, clients)
            candidates.extend(_record(c, item) for c in found)
            checks["judge"] = "ok"
        except ModelError as e:  # includes the budget: the judge is the LLM, which has no Bodhan cost
            checks["judge"] = f"error: {e}"

    deterministic = {
        "script": lambda: check_script(source, translation, expected),
        "numbers": lambda: check_numbers(source, translation, back),
        "format": lambda: check_format(source, translation),
        "entities": lambda: check_entities(source, translation, back),
    }
    for name, run in deterministic.items():
        if name == "entities" and back is None:
            checks[name] = "skipped: no back-translation"
            continue
        try:
            candidates.extend(_record(c, item) for c in run())
            checks[name] = "ok"
        except Exception as e:  # a bug or odd input in one check must not stop the run
            log.exception("%s/%s: %s check failed", item.id, item.lang, name)
            checks[name] = f"error: {type(e).__name__}: {e}"
    if item.reference:
        record["scores"] = reference_scores(translation, item.reference)
    return record


def score_speech(record: dict, clients: Clients, speech: bool) -> None:
    item, checks = record["_item"], record["checks"]
    record["status"] = dict(item.status)  # audio was generated after the text checks began
    if item.translation is None:
        return
    if not speech:
        checks["speech"] = "skipped: --skip-speech"
    elif item.audio is None:
        checks["speech"] = f"skipped: no audio ({item.status.get('audio', 'missing')})"
    else:
        try:
            found, record["speech"], statuses = check_speech(item.translation, item.audio, item.lang, clients,
                                                              clients.cache.root / "pieces")
            record["candidates"].extend(_record(c, item) for c in found)
            checks.update(statuses)
        except BudgetExceeded:
            checks["speech_asr"] = "skipped: budget"
        except ModelError as e:
            checks["speech_asr"] = f"error: {e}"


def _record(c: Candidate, item: Item) -> dict:
    """A candidate as JSON, with the text its span points at so a reader needn't count characters."""
    text = item.source if c.side == "source" else (item.translation or "")
    d = asdict(c)
    d["span"] = list(c.span)
    d["text"] = text[c.span[0]:c.span[1]]
    return d


def summarize(records: list[dict]) -> dict:
    """Counts for run.json: per language, items scored, with candidates, verdicts, MQM, flags by category."""
    out: dict = {}
    for lang in sorted({r["lang"] for r in records}):
        rs = [r for r in records if r["lang"] == lang]
        out[lang] = {
            "items": len(rs),
            "scored": sum(1 for r in rs if "translation" not in r["checks"]),
            "with_candidates": sum(1 for r in rs if r["candidates"]),
            **{v: sum(1 for r in rs if r["verdict"] == v) for v in ("flagged", "uncertain", "clean")},
            "mqm": sum(r["mqm"] for r in rs),
            "flags_by_category": dict(sorted(Counter(f["category"] for r in rs for f in r["flags"]
                                                     if f["decision"] == "flag").items())),
            "check_errors": sum(1 for r in rs for v in r["checks"].values() if v.startswith("error")),
        }
    return out
