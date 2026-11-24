"""pipeline: Run orchestration: dataset → passes → report.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .audio import AudioProfile, analyze, read_audio
from .calibration import calibrate, load_labels
from .checks import CheckContext, Flag, log_length_ratio, run_checks, speech_seconds_per_char
from .clients import DEFAULT_LLM_CONCURRENCY, DEFAULT_TTS_MAX_CHARS, BodhanClient, GenResult, LLMClient, LLMError, generate_missing, llm_concurrency_from_env
from .config import Settings, UnknownLanguageError, close_logging, load_settings, load_thresholds, parse_langs, setup_logging
from .dataset import Item, load_dataset
from .infra import Cache, CostLedger, RunStats, rate
from .judge import JUDGE_CHECK_ID, JudgeOutcome, counted_flag_agreement, fuse, judge_item, rerun_consistency, should_skip
from .report import JUDGE_UNAVAILABLE_REASONS, build_diff, build_sections, dump_json, load_items, render_diff_md, write_report
from .roundtrip import SIGNALS, RoundTrip, compare_back_translation, compare_transcript, compute_floors, run_round_trips

# -------------------- pipeline
log = logging.getLogger("bodhan_eval.pipeline")


@dataclass
class RunOptions:
    dataset: Path
    langs: list[str]
    out: Path
    generate: bool = False
    baseline: Path | None = None
    calibration: Path | None = None
    max_cost_inr: float | None = 50.0
    lifetime_cap_inr: float | None = None
    cache_dir: Path = Path(".bodhan_cache")
    offline: bool = False
    refresh_cache: bool = False
    concurrency: dict = field(default_factory=dict)
    thresholds: Path | None = None
    item_concurrency: int = 16
    tts_max_chars: int = DEFAULT_TTS_MAX_CHARS
    judge_max_items: int | None = None  # None = judge every eligible item; N = optional cap; 0 disables the judge
    judge_rerun: int = 0  # --judge-rerun N: consistency test on N judged items, cache bypassed
    llm_concurrency: int = DEFAULT_LLM_CONCURRENCY  # parallel judge calls (--llm-concurrency / LLM_CONCURRENCY)


@dataclass
class RunContext:
    opts: RunOptions
    settings: Settings
    cache: Cache
    ledger: CostLedger
    bodhan: BodhanClient
    stats: RunStats
    thresholds: dict


@dataclass
class Prepared:
    """Per-item inputs computed in pass 1 (shared by stats and checks)."""

    item: Item
    audio: AudioProfile | None = None
    audio_error: str | None = None
    generated: GenResult | None = None
    rt: RoundTrip | None = None
    cc: CheckContext | None = None
    tier1_flags: list = field(default_factory=list)
    records: list = field(default_factory=list)
    error: str | None = None  # an exception in this item's processing


def prepare_item(item: Item, thresholds: dict) -> Prepared:
    prep = Prepared(item)
    if item.audio is not None:
        try:
            samples, sr = read_audio(item.audio.read_bytes())
            prep.audio = analyze(samples, sr, thresholds)
        except FileNotFoundError:
            prep.audio_error = f"audio file not found: {item.audio.name}"
        except Exception as exc:  # undecodable audio is itself a finding
            prep.audio_error = f"audio not decodable: {type(exc).__name__}: {exc}"
    return prep


def collect_stats(preps: list[Prepared]) -> RunStats:
    stats = RunStats()
    for p in preps:
        it = p.item
        if it.translation:
            stats.add("log_length_ratio", it.lang, it.id, log_length_ratio(it.source, it.translation))
        if p.audio is not None:
            stats.add("noise_floor_db", it.lang, it.id, p.audio.floor_db)
            ctx = CheckContext(it.id, it.lang, it.source, audio=p.audio, audio_text=it.translation)
            stats.add("speech_s_per_char", it.lang, it.id, speech_seconds_per_char(ctx))
    return stats


def _sort_flags(flags: list[Flag]) -> list[dict]:
    return [f.to_dict() for f in sorted(flags, key=lambda f: (f.span is None, f.span or (0, 0), f.check_id, f.category))]


def run_tier1(ctx: RunContext, prep: Prepared) -> None:
    """Build the item's check context (with round-trip comparisons) and run Tier-1."""
    item, rt = prep.item, prep.rt or RoundTrip()
    cc = CheckContext(
        item_id=item.id, lang=item.lang, source=item.source, translation=item.translation, reference=item.reference,
        audio=prep.audio, audio_text=item.translation, stats=ctx.stats, thresholds=ctx.thresholds,
        back_translation=rt.back_translation, bt_truncated=rt.bt_truncated, asr_text=rt.asr_text,
    )
    if rt.back_translation:
        cc.bt = compare_back_translation(item.source, rt.back_translation, ctx.thresholds["asr_spelling_similarity"])
    if rt.asr_text is not None and item.translation:
        cc.asr = compare_transcript(item.translation, rt.asr_text, ctx.thresholds)
    flags, records = run_checks(cc, tier=1)
    if prep.audio_error:
        records.append({"check": "audio_load", "status": "error", "reason": prep.audio_error})
        if "not decodable" in prep.audio_error:
            flags.append(Flag("audio_load", "speech_garbled", "critical", None, "seconds", "Audio file cannot be decoded.", {"error": prep.audio_error}, 0.9))
    prep.cc, prep.tier1_flags, prep.records = cc, flags, records


def floor_observations(preps: list[Prepared]) -> list[tuple[str, str, float]]:
    """Signal values of CLEAN items: labelled clean (labels == []) when the dataset carries
    labels, otherwise items without any strong Tier-1 flag (a proxy)."""
    labelled = any(p.item.labels is not None for p in preps)
    obs = []
    for p in preps:
        if p.cc is None or p.error:
            continue
        clean = (p.item.labels == []) if labelled else not any(not f.needs_corroboration for f in p.tier1_flags)
        if not clean:
            continue
        if p.cc.bt is not None and not p.cc.bt_truncated:
            obs += [("bt_precision", p.item.lang, p.cc.bt.precision_masked), ("bt_recall", p.item.lang, p.cc.bt.recall_masked)]
        if p.cc.asr is not None:
            obs += [("asr_residual_wer", p.item.lang, p.cc.asr.residual_wer), ("asr_residual_cer", p.item.lang, p.cc.asr.residual_cer)]
    return obs


def finish_item(ctx: RunContext, prep: Prepared, floors: dict) -> dict:
    item, cc = prep.item, prep.cc
    cc.prior_flags = list(prep.tier1_flags)
    cc.floors = {sig: floors.get(sig, {}).get(item.lang, {}).get("value") for sig in SIGNALS}
    flags2, records2 = run_checks(cc, tier=2)
    out = {
        "id": item.id,
        "lang": item.lang,
        "status": "skipped_budget" if prep.generated is not None and prep.generated.budget_skipped else "ok",
        "inputs": {"reference": item.reference is not None, "translation": item.translation is not None, "audio": item.audio is not None},
        "route": prep.rt.route if prep.rt else [],
        "checks": prep.records + records2,
        "flags": _sort_flags(prep.tier1_flags + flags2),
        "metrics": cc.metrics,
    }
    if prep.rt is not None and (prep.rt.back_translation or prep.rt.asr_text is not None):
        out["roundtrip"] = {"back_translation": prep.rt.back_translation, "bt_truncated": prep.rt.bt_truncated, "asr_text": prep.rt.asr_text}
    if prep.generated is not None and prep.generated.to_dict():
        out["generated"] = prep.generated.to_dict()
    if item.audio_missing:
        out["skipped"] = [{"what": "speech_checks", "reason": "audio_missing"}]
    return out


def _error_record(prep: Prepared, error: str) -> dict:
    return {"id": prep.item.id, "lang": prep.item.lang, "status": "error", "error": error, "route": prep.rt.route if prep.rt else [], "checks": [], "flags": []}


def _isolated(prep: Prepared, fn, *args):
    """Run one item phase; an exception marks only this item."""
    if prep.error:
        return None
    try:
        return fn(*args)
    except Exception as exc:
        prep.error = f"{type(exc).__name__}: {exc}"
        log.exception("item_failed", extra={"fields": {"item": prep.item.id, "error": repr(exc)}})
        return None


def _verdict_summary(results: list[dict]) -> dict:
    counts: dict = {}
    noise_reasons: dict = {}
    routes: dict = {}
    for r in results:
        for v in r.get("metrics", {}).get("verdicts", []):
            c = counts.setdefault(v["signal"], {})
            c[v["verdict"]] = c.get(v["verdict"], 0) + 1
            if v["verdict"] == "checker_noise":
                for reason in v["reasons"]:
                    key = f"{v['signal']}:{reason}"
                    noise_reasons[key] = noise_reasons.get(key, 0) + 1
        for st in r.get("route", []):
            key = f"{st['stage']}:{'ran' if st['ran'] else 'skipped'}:{st['reason']}"
            routes[key] = routes.get(key, 0) + 1
    return {"verdicts": {k: dict(sorted(v.items())) for k, v in sorted(counts.items())},
            "checker_noise_reasons": dict(sorted(noise_reasons.items())), "routes": dict(sorted(routes.items()))}


def _flag_summary(results: list[dict]) -> dict:
    by_cat: dict = {}
    by_lang: dict = {}
    for r in results:
        strong = [f for f in r["flags"] if not f["needs_corroboration"]]
        for f in strong:
            by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
        lang = by_lang.setdefault(r["lang"], {"items": 0, "items_flagged": 0})
        lang["items"] += 1
        lang["items_flagged"] += bool(strong)
    return {"by_category": dict(sorted(by_cat.items())), "by_lang": dict(sorted(by_lang.items()))}


LLM_FAILURES_BEFORE_STOP = 3  # consecutive "judge unavailable" failures (e.g. dead endpoint, daily token cap) -> stop judging


def judge_disabled(opts: RunOptions) -> bool:
    return opts.judge_max_items is not None and opts.judge_max_items <= 0


def _judge_hints(rec: dict) -> list[str]:
    hints = []
    for f in rec["flags"]:
        if f["unit"] != "chars":
            continue
        if f["check_id"] == "back_translation" and f["category"] in ("mistranslation", "term_entity"):
            continue
        weak = ", weak" if f["needs_corroboration"] else ""
        hints.append(f"{f['check_id']} ({f['category']}{weak}): {f['message'][:200]}")
    bt = (rec.get("roundtrip") or {}).get("back_translation")
    if bt:
        hints.append(f"Back-translation of the translation into English: {bt[:600]}")
    return sorted(hints)


def _judge_order(candidates: list[dict]) -> list[dict]:
    """Round-robin across languages (each sorted by id) so a capped run covers every language."""
    by_lang: dict[str, list[dict]] = {}
    for r in sorted(candidates, key=lambda r: r["id"]):
        by_lang.setdefault(r["lang"], []).append(r)
    order, queues = [], [by_lang[k] for k in sorted(by_lang)]
    while any(queues):
        for q in queues:
            if q:
                order.append(q.pop(0))
    return order


async def run_judge_pass(llm: LLMClient | None, opts: RunOptions, results: list[dict], items: dict[str, Item]) -> list[dict]:
    """Pass 4. Records ``rec["judge"]`` on every ok item; returns re-judge jobs for the consistency test."""
    candidates = []
    for rec in results:
        if rec["status"] == "error":
            continue
        it = items[rec["id"]]
        skip = should_skip(rec["flags"])
        if not it.translation:
            rec["judge"] = JudgeOutcome("skipped", reason="no_translation").to_dict()
        elif skip is not None:  # checked before availability: these items would not be judged anyway
            rec["judge"] = JudgeOutcome("skipped", reason=skip).to_dict()
        elif llm is None:
            rec["judge"] = JudgeOutcome("skipped", reason="judge_disabled" if judge_disabled(opts) else "llm_not_configured").to_dict()
        else:
            candidates.append(rec)
    order = _judge_order(candidates)
    cap = len(order) if opts.judge_max_items is None else opts.judge_max_items
    for rec in order[cap:]:
        rec["judge"] = JudgeOutcome("skipped", reason="judge_max_items").to_dict()
    to_judge = order[:cap]

    async def judge_one(rec: dict) -> tuple[JudgeOutcome, list[str], bool]:
        """Returns (outcome, hints, unavailable). ``unavailable``: the LLM itself failed (retries
        exhausted, auth, connection) or answered invalid JSON even after the repair retry."""
        it = items[rec["id"]]
        hints = _judge_hints(rec)
        try:
            out = await judge_item(llm, source=it.source, translation=it.translation, lang=it.lang, hints=hints)
            unavailable = out.status == "error" and out.reason.startswith("invalid_json_after_repair")
        except LLMError as exc:
            out, unavailable = JudgeOutcome("error", reason=f"llm_error: {str(exc)[:200]}"), True
        except Exception as exc:  # e.g. offline cache miss: this item only, says nothing about the LLM
            out, unavailable = JudgeOutcome("error", reason=f"{type(exc).__name__}: {str(exc)[:200]}"), False
        return out, hints, unavailable

    # Ordered batches of ``llm_concurrency`` calls. Results are applied in the fixed order, so the
    # consecutive-failure count (and so where the judge stops) does not depend on call timing.
    jobs, consecutive, stopped = [], 0, False
    batch = max(1, opts.llm_concurrency)
    for start in range(0, len(to_judge), batch):
        chunk = to_judge[start:start + batch]
        if stopped:
            for rec in chunk:
                rec["judge"] = JudgeOutcome("skipped", reason="judge_unavailable").to_dict()
            continue
        outcomes = await asyncio.gather(*(judge_one(rec) for rec in chunk))
        for rec, (out, hints, unavailable) in zip(chunk, outcomes, strict=True):
            it = items[rec["id"]]
            if out.status == "ran":
                consecutive = 0  # a success (even after a 429 retry) resets the count
            elif unavailable:
                consecutive += 1
            log.info("judged", extra={"fields": {"item": rec["id"], "status": out.status, "verdict": out.verdict, "errors": len(out.errors),
                                                 "cached": out.cached, "tokens": out.tokens, "unavailable": unavailable}})
            rec["judge"] = out.to_dict()
            if out.status == "ran":
                jobs.append({"id": rec["id"], "lang": it.lang, "source": it.source, "translation": it.translation, "hints": hints, "outcome": out})
        if consecutive >= LLM_FAILURES_BEFORE_STOP:
            stopped = True
            log.warning("judge_stopped", extra={"fields": {"consecutive_failures": consecutive, "remaining": len(to_judge) - start - len(chunk)}})
    return jobs


def apply_fusion(rec: dict, thresholds: dict) -> None:
    """Pass 6: add judge flags; judge-alone / quote-not-found ones go to the review queue."""
    others = [f for f in rec.get("flags", []) if f["check_id"] != JUDGE_CHECK_ID]
    fused = fuse(others, (rec.get("judge") or {}).get("errors", []), thresholds)
    rec["flags"] = sorted(others + fused, key=lambda f: (f["span"] is None, f["span"] or [0, 0], f["check_id"], f["category"], f["message"]))
    rec["review_queue"] = [f for f in rec["flags"] if f["evidence"].get("review_queue")]


def _judge_summary(results: list[dict]) -> dict:
    status: dict = {}
    verdicts: dict = {}
    review = quotes = found = repaired = tokens = invalid = ran = 0
    for r in results:
        j = r.get("judge")
        if not j:
            continue
        reason = j.get("reason", "").split(":")[0]
        key = j["status"] if j["status"] == "ran" else f"{j['status']}:{reason}"
        status[key] = status.get(key, 0) + 1
        if j.get("verdict"):
            verdicts[j["verdict"]] = verdicts.get(j["verdict"], 0) + 1
        ran += j["status"] == "ran"
        invalid += reason == "invalid_json_after_repair"
        repaired += bool(j.get("repaired"))
        tokens += j.get("tokens", 0)
        quotes += len(j.get("errors", []))
        found += sum(e["quote_found"] for e in j.get("errors", []))
        review += len(r.get("review_queue", []))
    unavailable = {k: v for k, v in status.items() if k in JUDGE_UNAVAILABLE_REASONS}  # not deliberate skips
    return {"status": dict(sorted(status.items())), "verdicts": dict(sorted(verdicts.items())), "review_queue_flags": review,
            "quotes_not_found": quotes - found, "quote_found": rate(found, quotes), "json_valid": rate(ran, ran + invalid),
            "repaired": repaired, "tokens": tokens,
            # Items the judge should have read but could not (D2): the report states this in plain words.
            "unavailable": {"items_not_judged": sum(unavailable.values()), "by_reason": dict(sorted(unavailable.items()))}}


def _check_status(results: list[dict]) -> dict:
    """Counts of check records that did not run cleanly, as 'check: status' -> n."""
    out: dict = {}
    for r in results:
        for rec in r.get("checks", []):
            if rec.get("status") not in (None, "ok"):
                key = f"{rec['check']}: {rec['status']}"
                out[key] = out.get(key, 0) + 1
        for sk in r.get("skipped", []):
            key = f"{sk['what']}: skipped ({sk['reason']})"
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _cost_view(cost: dict) -> dict:
    """Cached-equivalent cost per service (identical on a first run and a cached rerun)."""
    per = {svc: {"calls": v["cached_calls"] + v["new_calls"], "units": v["cached_units"] + v["new_units"],
                 "unit": cost["units"].get(svc, ""), "inr": v["total_equivalent_inr"]} for svc, v in sorted(cost["per_service"].items())}
    return {"total_equivalent_inr": cost["total_equivalent_inr"], "per_service": per}


def _eval_labels(items: list[Item], cal_labels: dict | None) -> dict:
    """Ground truth for the report: the calibration file when given, else the dataset's own labels."""
    run_ids = {it.id for it in items}
    if cal_labels is not None:
        return {i: lbs for i, lbs in cal_labels.items() if i in run_ids}
    return {it.id: it.labels for it in items if it.labels is not None}


def _generation_summary(generated: dict[str, GenResult]) -> dict:
    counts = {"translations": 0, "audio": 0}
    for g in generated.values():
        counts["translations"] += g.translation is not None
        counts["audio"] += g.audio is not None
        for reason in g.skipped + g.errors:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


async def run(opts: RunOptions, settings: Settings) -> dict:
    t0 = time.perf_counter()
    loaded = load_dataset(opts.dataset, opts.langs)
    log.info("dataset_loaded", extra={"fields": {"items": len(loaded.items), "bad": len(loaded.bad), "filtered_out": loaded.filtered_out}})

    cache = Cache(opts.cache_dir / "cache.sqlite", read=not opts.refresh_cache)
    lifetime_before = sum(cache.lifetime_spend().values())
    ledger = CostLedger(
        settings.prices,
        max_run_inr=opts.max_cost_inr,
        lifetime_cap_inr=opts.lifetime_cap_inr,
        lifetime_spent_at_start=lifetime_before,
        spend_sink=cache.add_spend,
    )
    bodhan = BodhanClient(settings, cache, ledger, concurrency=opts.concurrency, offline=opts.offline)
    thresholds = load_thresholds(opts.thresholds)
    generated: dict[str, GenResult] = {}
    if opts.generate:  # pass 0: fill missing translations / audio (cached, budget-gated)
        gen_sem = asyncio.Semaphore(opts.item_concurrency)

        async def gen(it: Item) -> None:
            async with gen_sem:
                generated[it.id] = await generate_missing(bodhan, it, opts.out, opts.tts_max_chars)

        await asyncio.gather(*(gen(it) for it in loaded.items))
    preps = [prepare_item(it, thresholds) for it in loaded.items]  # pass 1
    for p in preps:
        p.generated = generated.get(p.item.id)
    stats = collect_stats(preps)
    ctx = RunContext(opts, settings, cache, ledger, bodhan, stats, thresholds)
    try:
        sem = asyncio.Semaphore(opts.item_concurrency)

        async def route(p: Prepared) -> None:  # pass 1b
            async with sem:
                try:
                    p.rt = await run_round_trips(bodhan, p.item, audio_decodable=p.audio is not None, generated=p.generated, generate=opts.generate)
                except Exception as exc:  # router failure: this item only
                    p.error = f"router: {type(exc).__name__}: {exc}"
                    log.exception("item_failed", extra={"fields": {"item": p.item.id, "error": repr(exc)}})

        await asyncio.gather(*(route(p) for p in preps))
    finally:
        await bodhan.aclose()
    for p in preps:  # pass 2
        _isolated(p, run_tier1, ctx, p)
    floors = compute_floors(floor_observations(preps), opts.langs, thresholds)
    opts.out.mkdir(parents=True, exist_ok=True)
    generated_th = {"floors": floors, "rule_priors": {k: thresholds[k] for k in sorted(thresholds)}}
    (opts.out / "thresholds.generated.json").write_text(dump_json(generated_th), encoding="utf-8")
    results = []
    for p in preps:  # pass 3
        rec = _isolated(p, finish_item, ctx, p, floors)
        results.append(rec if rec is not None else _error_record(p, p.error or "unknown error"))
    results.sort(key=lambda r: (r["lang"], r["id"]))

    llm = None
    if not judge_disabled(opts) and settings.llm_configured:
        llm = LLMClient(settings, cache, ledger, concurrency=opts.llm_concurrency, offline=opts.offline)
    consistency = None
    try:
        jobs = await run_judge_pass(llm, opts, results, {p.item.id: p.item for p in preps})  # pass 4
        if llm is not None and opts.judge_rerun > 0:
            consistency = await rerun_consistency(llm, jobs[: opts.judge_rerun], opts.langs)
    finally:
        if llm is not None:
            await llm.aclose()

    fusion_th = dict(thresholds)  # pass 5
    calibration: dict = {"status": "not_run", "reason": "no --calibration file given"}
    cal_labels, label_info, unmatched_cal = None, None, []
    if opts.calibration:
        try:
            cal_labels, label_info = load_labels(opts.calibration)
            run_ids = {it.id for it in loaded.items}
            unmatched_cal = sorted(set(cal_labels) - run_ids)
            if unmatched_cal:  # e.g. another language than --langs, or a typo in the ids
                log.warning("calibration_ids_not_in_run", extra={"fields": {"count": len(unmatched_cal), "examples": unmatched_cal[:20]}})
            fusion_th, th_json, calibration = calibrate(results, cal_labels, label_info, thresholds, opts.langs)
            (opts.out / "thresholds.json").write_text(dump_json(th_json), encoding="utf-8")
        except Exception as exc:  # a broken label file never stops the report
            log.exception("calibration_failed", extra={"fields": {"error": repr(exc)}})
            calibration = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    for rec in results:  # pass 6
        if rec["status"] != "error":
            apply_fusion(rec, fusion_th)
    if consistency is not None:
        counted_flag_agreement(consistency, results, fusion_th)

    item_by_id = {p.item.id: p.item for p in preps}
    labels = _eval_labels(loaded.items, cal_labels)
    if label_info is None and labels:
        label_info = {"by_source": {}}
        for lbs in labels.values():
            for lb in lbs:
                src = str(lb.extra.get("source") or "unspecified")
                label_info["by_source"][src] = label_info["by_source"].get(src, 0) + 1
        label_info["by_source"] = dict(sorted(label_info["by_source"].items()))
    sections = build_sections(results, item_by_id, labels, label_info, fusion_th, opts.langs, calibration, consistency)
    diff_md, diff = None, None
    if opts.baseline:  # pass 7: baseline diff
        base = load_items(opts.baseline)
        if base is None:
            diff_md = f"# Baseline diff\n\nBaseline `{opts.baseline.name}` has no items.jsonl; nothing to compare.\n"
        else:
            diff = build_diff(base, results, labels)
            diff_md = render_diff_md(diff, opts.baseline.name)

    cost = ledger.summary()
    item_errors = sum(1 for r in results if r["status"] == "error")
    summary = {
        "dataset": {
            "path": opts.dataset.name,
            "langs": sorted(opts.langs),
            "total_lines": loaded.total_lines,
            "evaluated": len(results),
            "bad_rows": len(loaded.bad),
            "bad_row_details": loaded.bad,
            "filtered_out": loaded.filtered_out,
            "missing_audio": len(loaded.missing_audio),
            "missing_audio_ids": loaded.missing_audio,
            "item_errors": item_errors,
            "bad_labels": len(loaded.bad_labels),
            "bad_label_details": loaded.bad_labels,
            "calibration_ids_not_in_run": len(unmatched_cal),
            "calibration_ids_not_in_run_examples": unmatched_cal[:20],
        },
        "flags": _flag_summary(results),
        "check_status": _check_status(results),
        "roundtrip": _verdict_summary(results),
        "judge": _judge_summary(results),
        "fusion_thresholds": {k: v for k, v in sorted(fusion_th.items()) if k.startswith("judge_min_confidence")},
        "calibration_heldout": calibration.get("heldout", {}).get("overall"),
        "generation": _generation_summary(generated) if opts.generate else None,
        "run_stats": stats.summary(),
        # Report body carries only cached-equivalent totals so a cached rerun is byte-identical.
        "cost": _cost_view(cost),
        **sections,
    }
    if consistency is not None:
        summary["judge_consistency"] = consistency
    if diff is not None:
        summary["baseline_diff"] = {k: diff[k] for k in ("items", "totals", "overall", "counts", "flag_counts_by_category", "significant_regression")}
    run_meta = {
        "version": __version__,
        "settings": settings.redacted(),
        "options": {
            "generate": opts.generate,
            "tts_max_chars": opts.tts_max_chars,
            "max_cost_inr": opts.max_cost_inr,
            "lifetime_cap_inr": opts.lifetime_cap_inr,
            "offline": opts.offline,
            "baseline": str(opts.baseline) if opts.baseline else None,
            "calibration": str(opts.calibration) if opts.calibration else None,
        },
        "cost": cost,
        "lifetime_spend_inr_before": round(lifetime_before, 6),
        "lifetime_spend_inr_after": round(sum(cache.lifetime_spend().values()), 6),
        "cache": cache.stats(),
        "runtime_s": round(time.perf_counter() - t0, 2),
    }
    cache.close()
    write_report(opts.out, items=results, summary=summary, calibration=calibration, run_meta=run_meta, diff_md=diff_md)
    log.info(
        "run_done",
        extra={"fields": {"items": len(results), "new_inr": cost["new_inr"], "cached_equivalent_inr": cost["cached_equivalent_inr"], "out": str(opts.out)}},
    )
    return {"summary": summary, "run_meta": run_meta}


# -------------------- cli
log = logging.getLogger("bodhan_eval.pipeline")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval", description="Translation and speech quality harness for Bodhan Indic-Translate / Indic-Speak.")
    p.add_argument("--dataset", required=True, type=Path, help="JSONL dataset (id, source, lang required)")
    p.add_argument("--langs", required=True, help="comma-separated language codes, e.g. hi,ta,bn")
    p.add_argument("--out", required=True, type=Path, help="report output directory")
    p.add_argument("--generate", action="store_true", help="generate missing translations/audio with Bodhan models")
    p.add_argument("--baseline", type=Path, help="previous report directory to diff against")
    p.add_argument("--calibration", type=Path, help="JSONL with human labels for calibration")
    p.add_argument("--max-cost-inr", type=float, default=50.0, help="cap on NEW model spend in this run (default 50)")
    # extras
    p.add_argument("--lifetime-cap-inr", type=float, default=None, help="hard cap on total real spend across all runs sharing this cache")
    p.add_argument("--cache-dir", type=Path, default=Path(".bodhan_cache"), help="cache directory (default .bodhan_cache)")
    p.add_argument("--offline", action="store_true", help="never call a model; use cached outputs only")
    p.add_argument("--refresh-cache", action="store_true", help="ignore cached outputs (still writes new ones)")
    p.add_argument("--thresholds", type=Path, default=None, help="thresholds.json written by a calibration run (default: built-in priors)")
    p.add_argument("--env-file", type=Path, default=None, help="explicit .env file (default: nearest .env upward from cwd)")
    p.add_argument(
        "--tts-max-chars",
        type=int,
        default=DEFAULT_TTS_MAX_CHARS,
        help="--generate synthesizes audio only for translations up to this many characters (default 110)",
    )
    p.add_argument(
        "--judge-max-items", type=int, default=None, help="optional cap on items the LLM judge reads (default: every eligible item; 0 disables the judge)"
    )
    p.add_argument(
        "--llm-concurrency",
        type=int,
        default=None,
        help=f"parallel LLM judge calls (default: LLM_CONCURRENCY env var, else {DEFAULT_LLM_CONCURRENCY}; use 1 for tight rate limits)",
    )
    p.add_argument("--judge-rerun", type=int, default=0, help="consistency test: re-judge N judged items (separate, cached sample)")
    p.add_argument("--ci", action="store_true", help="exit 1 on a significant regression vs --baseline (exact McNemar) or on failed items")
    p.add_argument("--log-level", default="INFO", help="console log level (file log is always DEBUG)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        langs, warnings = parse_langs(args.langs)
    except UnknownLanguageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.dataset.exists():
        print(f"error: dataset not found: {args.dataset}", file=sys.stderr)
        return 2
    setup_logging(args.out, args.log_level)
    for w in warnings:
        log.warning(w)
    settings = load_settings(args.env_file)
    opts = RunOptions(
        dataset=args.dataset,
        langs=langs,
        out=args.out,
        generate=args.generate,
        baseline=args.baseline,
        calibration=args.calibration,
        max_cost_inr=args.max_cost_inr,
        lifetime_cap_inr=args.lifetime_cap_inr,
        cache_dir=args.cache_dir,
        offline=args.offline,
        refresh_cache=args.refresh_cache,
        thresholds=args.thresholds,
        tts_max_chars=args.tts_max_chars,
        judge_max_items=args.judge_max_items,
        judge_rerun=args.judge_rerun,
        llm_concurrency=max(1, args.llm_concurrency) if args.llm_concurrency else llm_concurrency_from_env(),
    )
    try:
        result = asyncio.run(run(opts, settings))
    finally:
        close_logging()
    if args.ci:  # CI gate: fail on a significant regression vs --baseline, or on failed items
        s = result["summary"]
        if (s.get("baseline_diff") or {}).get("significant_regression") or s["dataset"]["item_errors"]:
            print("ci: significant regression vs baseline or failed items", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
