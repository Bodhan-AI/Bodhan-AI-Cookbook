"""Orchestration: load, run checks stage by stage across the dataset, write the report."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import calibrate, config, dataset, generate, report
from . import checks_speech as cs
from .cache import Cache
from .checks_roundtrip import agreement, back_translation, regen_candidates, regenerate
from .checks_text import MIN_ITEMS_FOR_STATS, OUTLIER_Z, TEXT_CHECKS, chrf, length_candidates, reference_scores, robust_z
from .clients import KEY_ENV, LLM, Bodhan, Budget, NotRun, RunLog
from .combine import SPAN_SIDE, combine
from .judge import judge_item


def parse_langs(value: str) -> tuple[set[str], list[str]]:
    codes, warnings = set(), []
    for part in (value or "").split(","):
        if not part.strip():
            continue
        code = config.normalize_lang(part)
        if code and code != "en":
            codes.add(code)
        else:
            warnings.append(f"--langs: unknown language {part.strip()!r} ignored")
    return codes, warnings


SKIPPABLE = {"back_translation", "regen_diff", "audio_stats", "transcript", "judge"}


def build_clients(args, log: RunLog, cache: Cache, env=os.environ):
    keys = {svc: env.get(name, "").strip() for svc, name in KEY_ENV.items()}
    bodhan = Bodhan(keys, cache, Budget(args.max_cost_inr), log, concurrency=args.concurrency)
    llm = LLM(env.get("LLM_BASE_URL"), env.get("LLM_API_KEY"), env.get("LLM_MODEL"), cache, log, concurrency=args.concurrency)
    return bodhan, llm


def run(args, bodhan=None, llm=None, env=os.environ) -> int:
    out = Path(args.out)
    log = RunLog()
    cache = Cache(args.cache_dir)
    if bodhan is None or llm is None:
        b, m = build_clients(args, log, cache, env)
        bodhan, llm = bodhan or b, llm or m
    bodhan.log = llm.log = log  # injected clients report into this run's log

    langs, warnings = parse_langs(args.langs)
    if not langs:
        warnings.append("--langs named no supported language; nothing scored")
    items, skipped = dataset.load(args.dataset, langs)
    missing = [name for svc, name in KEY_ENV.items() if not bodhan.keys.get(svc)]
    if missing:
        warnings.append("missing Bodhan keys, related checks not run: " + ", ".join(missing))
    if not llm.available:
        warnings.append("LLM_BASE_URL / LLM_MODEL not set: the LLM judge did not run")

    results = [{"id": it["id"], "lang": it["lang"], "status": "clean", "checks_run": {}, "flags": [], "review": [],
                "scores": {}, "notes": list(it["notes"]), "_cands": []} for it in items]

    skip = {x.strip() for x in (getattr(args, "skip", None) or "").split(",") if x.strip()}
    if skip - SKIPPABLE:
        warnings.append(f"--skip: unknown checks ignored: {', '.join(sorted(skip - SKIPPABLE))}")

    def run_check(name, thunk, item, res):
        """Run one check; record ok / not_run / error. Returns the thunk's value or None."""
        if name in skip:
            res["checks_run"][name] = "not_run: disabled by --skip"
            return None
        try:
            value = thunk()
            res["checks_run"][name] = "ok"
            return value
        except NotRun as e:
            res["checks_run"][name] = f"not_run: {e.reason}"
        except Exception as e:  # a bug in one check must not stop the run
            res["checks_run"][name] = "error"
            log.event(kind="check_error", check=name, item=f"{item['id']}/{item['lang']}", error=repr(e)[:300])
        return None

    if args.generate:
        generate.fill(items, results, bodhan, out / "audio", run_check, args.concurrency, args.max_cost_inr)
        for item, res in zip(items, results, strict=True):
            made = [k for k, flag in (("translation", "generated"), ("audio", "audio_generated")) if item.get(flag)]
            if made:
                res["generated"] = made

    # Tier 1: code-only checks.
    with_tr = [i for i, it in enumerate(items) if it["translation"]]
    for item, res in zip(items, results, strict=True):
        for name, fn in TEXT_CHECKS.items():
            if item["translation"]:
                res["_cands"] += run_check(name, lambda fn=fn, item=item: fn(item), item, res) or []
            else:
                res["checks_run"][name] = "not_applicable"
    length_c, length_langs = length_candidates([items[i] for i in with_tr])
    ref_scores, ref_c = reference_scores([items[i] for i in with_tr])
    for k, i in enumerate(with_tr):
        res = results[i]
        res["checks_run"]["length"] = "ok" if items[i]["lang"] in length_langs else "not_run: too few items in this language"
        res["_cands"] += length_c.get(k, [])
        if k in ref_scores:
            res["checks_run"]["reference"] = "ok"
            res["scores"]["chrf_ref"] = ref_scores[k]
            res["_cands"] += ref_c.get(k, [])
        else:
            res["checks_run"]["reference"] = "not_applicable"

    # Tier 2: Bodhan round trips, in parallel across items (each worker touches only its own result).
    regens = {}

    def tier2(i):
        item, res = items[i], results[i]
        ctx = {"item": f"{item['id']}/{item['lang']}"}
        bt = run_check("back_translation", lambda: back_translation(item, bodhan, {**ctx, "check": "back_translation"}), item, res)
        if bt:
            res["_cands"] += bt["candidates"]
            res["round_trip"] = {"back_translation": bt["back_translation"]}
            res["scores"]["chrf_backtranslation"] = chrf(bt["back_translation"], item["source"])
        if item.get("generated"):
            res["checks_run"]["regen_diff"] = "not_applicable"  # we made this translation ourselves
        else:
            rg = run_check("regen_diff", lambda: regenerate(item, bodhan, {**ctx, "check": "regen_diff"}), item, res)
            if rg:
                regens[i] = rg

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        list(pool.map(tier2, with_tr))
    agree = {}
    for lang in sorted({items[i]["lang"] for i in regens}):
        agree[lang] = agreement([regens[i] for i in sorted(regens) if items[i]["lang"] == lang])
    for i, rg in regens.items():
        results[i]["scores"]["regen_similarity"] = rg["similarity"]
        results[i]["_cands"] += regen_candidates(items[i], rg, agree[items[i]["lang"]])
        results[i]["_fresh"] = rg["fresh"]  # for the judge; not written to the report

    # Tier 2: speech. Audio statistics need no model; the transcript comparison needs Indic-Transcribe.
    rates = []

    def speech(i):
        item, res = items[i], results[i]
        ctx = {"item": f"{item['id']}/{item['lang']}", "check": "transcript"}
        if not item.get("audio") and not item.get("audio_bytes"):
            reason = "not_run: audio file not found" if item.get("audio_declared") else "not_applicable"
            res["checks_run"]["audio_stats"] = res["checks_run"]["transcript"] = reason
            return

        def load_and_measure():
            data = item.get("audio_bytes") or Path(item["audio"]).read_bytes()
            x, sr = cs.read_samples(cs.to_wav(data, Path(item.get("audio") or "audio").name))
            return x, sr, cs.audio_stats(x, sr)

        got = run_check("audio_stats", load_and_measure, item, res)
        if not got:
            res["checks_run"]["transcript"] = "not_run: audio unreadable"
            return
        x, sr, st = got
        res["_cands"] += cs.stats_candidates(st)
        res["audio"] = {k: st[k] for k in ("duration_s", "voiced_s", "longest_silence_s", "clipped_share", "contrast")}
        if not item["translation"]:
            res["checks_run"]["transcript"] = "not_applicable"  # nothing to compare the speech with
            return
        if st["voiced_s"] == 0 or st["peak_rms"] < 0.003:
            res["checks_run"]["transcript"] = "not_run: audio is silent"  # nothing to transcribe; don't pay for it
            return
        rates.append((i, st["voiced_s"] / max(1, cs.spoken_chars(item["translation"])), True))
        tr = run_check("transcript", lambda: cs.transcribe_long(x, sr, item["lang"], bodhan, ctx), item, res)
        if tr:
            al = cs.align(item["translation"], tr[0])
            res["_cands"] += cs.align_candidates(al, st)
            res["scores"]["speech_similarity"] = al["similarity"]
            res["audio"]["transcript"] = tr[0]

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        list(pool.map(speech, range(len(items))))
    by_lang = {}
    for i, r, ok in rates:
        by_lang.setdefault(items[i]["lang"], []).append((i, r, ok))
    for rows in by_lang.values():
        for i, c in cs.speaking_rate_candidates(sorted(rows), robust_z, MIN_ITEMS_FOR_STATS, OUTLIER_Z).items():
            results[i]["_cands"] += c

    # Tier 3: the judge, for meaning. Code candidates are passed in as hints for it to confirm or reject.
    def tier3(i):
        item, res = items[i], results[i]
        hints = [c for c in res["_cands"] if c["class"] != "weak" and not c["category"].startswith(("speech", "garbled"))]
        bt = (res.get("round_trip") or {}).get("back_translation")
        j = run_check("judge", lambda: judge_item(item, llm, hints, bt, log), item, res)
        if j:
            res["_cands"] += j["candidates"]
            res["judge"] = {"pass1": j["pass1"], "pass2": j["pass2"], "consistent": j["consistent"]}

    if llm.available:
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            list(pool.map(tier3, with_tr))
    else:
        for i in with_tr:
            results[i]["checks_run"]["judge"] = "not_run: no LLM configured"
    judged = {}
    for res in results:
        if (res.get("judge") or {}).get("consistent") is not None:
            judged.setdefault(res["lang"], []).append(res["judge"]["consistent"])
    consistency = {lang: {"items": len(v), "same_verdict": round(sum(v) / len(v), 3)} for lang, v in sorted(judged.items())}

    # Calibrate on labelled items, then decide every item with the same thresholds.
    labels, label_sources, label_warnings = calibrate.collect(items, args.calibration)
    warnings += label_warnings
    thresholds = calibrate.fit_thresholds(results, labels)
    noise = 0
    for res in results:
        if not any(v == "ok" for v in res["checks_run"].values()):
            res["status"] = "unscored"
        else:
            j = res.get("judge") or {}
            verdict = "clean" if j.get("pass1") == "clean" and j.get("pass2") in (None, "clean") else None
            res["status"], res["flags"], res["review"], dropped = combine(calibrate.apply(res["_cands"], thresholds), verdict)
            if dropped:
                res["dropped_as_noise"] = dropped
                noise += dropped
    cal = calibrate.evaluate(results, labels, thresholds, label_sources, SPAN_SIDE)
    cal["judge"] = {"consistency": consistency}
    cal["dropped_as_checker_noise"] = noise
    for res in results:
        for k in [k for k in res if k.startswith("_")]:
            del res[k]

    meta = {"langs": sorted(langs), "warnings": warnings, "regen_agreement": agree, "judge_consistency": consistency,
            "calibration": cal}
    texts = {(it["id"], it["lang"]): (it["source"], it["translation"] or "") for it in items}
    diff = report.write(out, results, skipped, meta, log, texts, args.baseline)
    with open(out / "run_log.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for e in log.events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"scored {len(results)} items, skipped {len(skipped)}; report in {out}")
    limit = getattr(args, "fail_on_regression", None)
    if limit is not None and diff.get("items_with_new_major_or_critical", 0) > limit:
        print(f"regression: {diff['items_with_new_major_or_critical']} items have a new major or critical flag (limit {limit})")
        return 1
    return 0
