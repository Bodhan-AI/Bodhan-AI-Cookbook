"""Run orchestration: load the dataset, run every check per item, combine, write results.

Per item, in order (cheapest evidence first; later steps use earlier evidence):
  1. translation  (supplied, or generated with --generate)
  2. free checks  (script, numbers, terms, format, length, chrF++ vs reference)
  3. back-translation (paid; also re-weights the number check)
  4. speech       (supplied audio, or synthesized with --generate; waveform + transcription)
  5. LLM judge    (sees the earlier flags as hints)
  6. combine      (merge candidates, apply thresholds)

A paid step that cannot run (budget exhausted, offline cache miss, API error) is recorded as
skipped with its reason; the item keeps every result it already has. The run never aborts
because of one item.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from bodhan_eval import report
from bodhan_eval.cache import Cache
from bodhan_eval.calibrate import (
    CalItem,
    LabelRec,
    calibrate,
    category_accuracy,
    evaluate,
    thresholds_from_file,
)
from bodhan_eval.categories import CATEGORIES, area_of
from bodhan_eval.checks.audio import check_audio
from bodhan_eval.checks.backtranslation import check_backtranslation, reconcile_numbers
from bodhan_eval.checks.base import CheckResult, Flag
from bodhan_eval.checks.reference import check_reference
from bodhan_eval.checks.speech import (
    SpeechSegment,
    check_speech,
    concat_wavs,
    synthesize,
    to_wav,
)
from bodhan_eval.checks.translation import run_translation_checks
from bodhan_eval.clients import BodhanClient, LLMOutputError, make_llm_client
from bodhan_eval.clients.http import APIError, CacheMiss
from bodhan_eval.combine import apply_thresholds, combine, item_verdict, threshold_for
from bodhan_eval.cost import BudgetExceeded, CostMeter
from bodhan_eval.dataset import Item, Label, load_dataset, parse_labels
from bodhan_eval.judge.judge import consistency_sample, judge_item, judge_repeats
from bodhan_eval.languages import Language, load_languages, resolve_langs
from bodhan_eval.logs import event, setup_logging
from bodhan_eval.params import Params

log = logging.getLogger("bodhan_eval.pipeline")

# Errors that skip one paid step but never stop the run.
SOFT_ERRORS = (BudgetExceeded, CacheMiss, APIError, LLMOutputError)
HINT_MIN_CONFIDENCE = 0.3


@dataclass
class ItemResult:
    item: Item
    translation: str | None = None
    translation_generated: bool = False
    spoken_text: str | None = None
    checks: list[CheckResult] = field(default_factory=list)
    candidates: list[Flag] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    judge_inputs: dict[str, Any] | None = None  # reused by the consistency re-judgements
    audio_path: str | None = None  # supplied audio, or where generated audio was saved

    def to_dict(self) -> dict[str, Any]:
        it = self.item
        return {
            "id": it.id,
            "lang": it.lang,
            "source": it.source,
            "translation": self.translation,
            "translation_generated": self.translation_generated,
            "spoken_text": self.spoken_text,
            "audio": self.audio_path or (str(it.audio) if it.audio else None),
            **item_verdict(self.flags),
            "flags": [f.to_dict() for f in self.flags],
            "candidates": [f.to_dict() for f in self.candidates],
            "checks": [c.to_dict() for c in self.checks],
            "skipped": self.skipped,
            "labels": (
                [
                    {"category": lb.category, "severity": lb.severity, "span": lb.span}
                    for lb in it.labels
                ]
                if it.labels is not None
                else None
            ),
        }


def _soft_reason(e: BaseException) -> str:
    if isinstance(e, BudgetExceeded):
        return "budget exhausted"
    if isinstance(e, CacheMiss):
        return "offline: not in cache"
    return f"{type(e).__name__}: {e}"


def _speech_selected(items: list[Item], spec: float | None, seed: int) -> set[str]:
    ids = [it.id for it in items]
    if spec is None:
        return set(ids)
    k = int(spec) if spec > 1 else max(1, round(spec * len(ids)))
    return set(consistency_sample(ids, k, seed=seed + 1))


class Runner:
    def __init__(self, args, params: Params, registry: dict[str, Language]):
        self.args = args
        self.params = params
        self.registry = registry
        self.cache = Cache(args.cache_dir / "cache.sqlite")
        self.cost = CostMeter(max_inr=args.max_cost_inr)
        self.bodhan = BodhanClient(
            self.cache, self.cost, concurrency=args.concurrency, offline=args.offline
        )
        self.llm = None
        if not args.no_judge:
            llm = make_llm_client(
                self.cache, self.cost, concurrency=args.concurrency, offline=args.offline
            )
            if llm.configured:
                self.llm = llm
        self._sem = asyncio.Semaphore(args.concurrency)
        self.done = 0
        self.total = 0

    async def aclose(self) -> None:
        await self.bodhan.aclose()
        self.cache.close()

    async def _paid(self, r: ItemResult, step: str, coro):
        """Await a paid step; on a soft error record why it was skipped and return None."""
        try:
            return await coro
        except SOFT_ERRORS as e:
            r.skipped[step] = _soft_reason(e)
            event(
                log,
                "step_skipped",
                logging.WARNING,
                item_id=r.item.id,
                step=step,
                reason=r.skipped[step],
            )
            return None

    async def run_item(self, item: Item, speech: bool) -> ItemResult:
        async with self._sem:
            r = await self._run_item(item, speech)
        self.done += 1
        if self.args.log_level.upper() != "ERROR":
            v = item_verdict(r.flags)["verdict"]
            skipped = f" (skipped: {', '.join(sorted(r.skipped))})" if r.skipped else ""
            print(
                f"[{self.done}/{self.total}] {item.id}: {v}{skipped}", file=sys.stderr, flush=True
            )
        return r

    async def _run_item(self, item: Item, speech: bool) -> ItemResult:
        lang = self.registry[item.lang]
        r = ItemResult(item)
        P = self.params

        # 1. translation
        tr = item.translation
        if tr is None and self.args.generate:
            res = await self._paid(
                r, "translate", self.bodhan.translate(item.source, lang.translate_code)
            )
            if res is not None:
                tr, r.translation_generated = res.text, True
                if res.truncated:
                    r.checks.append(
                        CheckResult(
                            "api",
                            flags=[
                                Flag(
                                    "omission",
                                    "major",
                                    "api",
                                    0.9,
                                    (max(0, len(tr) - 1), len(tr)),
                                    "Indic-Translate reported the output as truncated",
                                    details={"kind": "truncation"},
                                )
                            ],
                        )
                    )
        r.translation = tr

        if tr:
            # 2. free checks
            free = run_translation_checks(item.source, tr, lang, P)
            r.checks.extend(free)
            r.checks.append(check_reference(tr, item.reference, P))
            # 3. back-translation
            bt = await self._paid(
                r, "backtranslation", check_backtranslation(self.bodhan, item.source, tr, lang, P)
            )
            if bt is not None:
                r.checks.append(bt)
                numbers = next((c for c in free if c.check == "numbers"), None)
                if numbers is not None:
                    reconcile_numbers(numbers, bt)
        else:
            r.skipped["translation_checks"] = "no translation (use --generate to create one)"

        # 4. speech
        r.spoken_text = item.extra.get("speech_text") or tr
        if speech:
            await self._speech(r, lang)
        elif item.audio is not None or self.args.generate:
            r.skipped["speech"] = "not in --speech-sample"

        # 5. judge
        if tr and self.llm is not None:
            bt_res = next((c for c in r.checks if c.check == "backtranslation"), None)
            hints = [
                f
                for c in r.checks
                for f in c.flags
                if f.confidence >= HINT_MIN_CONFIDENCE and area_of(f.category) == "translation"
            ]
            r.judge_inputs = {
                "back_translation": (
                    " ".join(bt_res.signals["back_translation"]) if bt_res else None
                ),
                "hints": hints,
            }
            j = await self._paid(
                r, "judge", judge_item(self.llm, item.source, tr, lang, **r.judge_inputs)
            )
            if j is not None:
                r.checks.append(j)
        elif tr:
            r.skipped["judge"] = (
                "disabled (--no-judge)" if self.args.no_judge else "LLM not configured"
            )

        # 6. combine
        r.candidates = combine([f for c in r.checks for f in c.flags], P)
        r.flags = apply_thresholds(r.candidates, P)
        event(
            log,
            "item_done",
            item_id=item.id,
            lang=item.lang,
            flags=len(r.flags),
            candidates=len(r.candidates),
            skipped=sorted(r.skipped),
        )
        return r

    async def _speech(self, r: ItemResult, lang: Language) -> None:
        item, text = r.item, r.spoken_text
        if not text:
            r.skipped["speech"] = "no text to compare the audio with"
            return
        if item.audio is not None:
            segments = [SpeechSegment(item.audio.read_bytes())]
        elif self.args.generate:
            segments = await self._paid(
                r, "speak", synthesize(self.bodhan, text, lang, self.args.voice or lang.voice)
            )
            if not segments:
                return
        else:
            return
        try:
            x, sr = concat_wavs([s.wav for s in segments])
            if item.audio is None:  # generated: keep it so it can be listened to / re-used
                path = self.args.out / "audio" / f"{item.id}.wav"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(to_wav(x, sr))
                r.audio_path = str(path.resolve())
        except Exception as e:  # unreadable audio is a finding, not a crash
            r.checks.append(
                CheckResult(
                    "audio",
                    flags=[
                        Flag(
                            "speech_garbled",
                            "critical",
                            "audio",
                            0.95,
                            (0, len(text)),
                            f"audio could not be decoded: {e}",
                        )
                    ],
                )
            )
            return
        audio = check_audio(x, sr, text, self.params, lang.chars_per_second)
        r.checks.append(audio)
        sp = await self._paid(
            r,
            "transcribe",
            check_speech(
                self.bodhan,
                text,
                lang,
                self.params,
                segments,
                audio_flags=audio.flags,
                duration=len(x) / sr if sr else None,
            ),
        )
        if sp is not None:
            r.checks.append(sp)

    async def consistency(self, results: list[ItemResult]) -> dict[str, Any]:
        if self.llm is None or self.args.judge_repeats < 2:
            return {"ran": False}
        judged = [r for r in results if any(c.check == "judge" for c in r.checks)]
        k = min(len(judged), max(3, round(0.1 * len(judged))), 20)
        ids = set(consistency_sample([r.item.id for r in judged], k, seed=self.args.seed))
        per_item = {}
        for r in judged:
            if r.item.id not in ids:
                continue
            lang = self.registry[r.item.lang]
            try:
                _, ag = await judge_repeats(
                    self.llm,
                    r.item.source,
                    r.translation,
                    lang,
                    self.args.judge_repeats,
                    **(r.judge_inputs or {}),
                )
            except SOFT_ERRORS as e:
                per_item[r.item.id] = {"skipped": _soft_reason(e)}
                continue
            per_item[r.item.id] = ag
        done = [a for a in per_item.values() if a.get("verdict") is not None]
        return {
            "ran": True,
            "items": len(per_item),
            "repeats": self.args.judge_repeats,
            "verdict_agreement": round(sum(a["verdict"] for a in done) / len(done), 4)
            if done
            else None,
            "flag_agreement": round(sum(a["flags"] for a in done) / len(done), 4) if done else None,
            "per_item": per_item,
        }


def load_human_labels(path, loaded) -> dict[str, list[Label]]:
    """--calibration JSONL: {"id": ..., "labels": [...]} per line; complete human labels."""
    out: dict[str, list[Label]] = {}
    for line_no, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        labs = parse_labels(row.get("labels"), line_no, str(row.get("id")), loaded.problems)
        if row.get("id") is not None and labs is not None:
            out[str(row["id"])] = labs
    return out


def _cal_items(results: list[ItemResult], human: dict[str, list[Label]]) -> list[CalItem]:
    items = []
    for r in results:
        it = r.item
        if it.id in human:
            labels, scope = human[it.id], "complete"
        elif it.labels is not None:
            labels, scope = it.labels, it.extra.get("label_scope") or "complete"
        else:
            continue
        recs = [LabelRec(lb.category, lb.span, bool(lb.raw.get("span_approx"))) for lb in labels]
        items.append(CalItem(it.id, scope, recs, r.candidates))
    return items


def run_calibration(results, params: Params, human, *, fixed: bool) -> dict[str, Any] | None:
    """Calibrate thresholds on labelled items (or, with fixed thresholds, only evaluate) and
    re-apply the thresholds to every item. None when nothing is labelled."""
    items = _cal_items(results, human)
    if not items:
        return None
    if fixed:
        th = {c: threshold_for(c, params) for c in CATEGORIES}
        cal = {
            "mode": "evaluate (fixed thresholds from --thresholds)",
            "thresholds": th,
            "metrics": evaluate(items, th),
            "category_accuracy": category_accuracy(items),
            "max_clean_flag_rate": params.get("calibration", "max_clean_flag_rate", 0.10),
            "in_sample": False,
        }
    else:
        cal = {"mode": "calibrate", **calibrate(items, params)}
    applied = params.with_overrides({"combine": {"thresholds": cal["thresholds"]}})
    for r in results:
        r.flags = apply_thresholds(r.candidates, applied)
    return cal


def summarize(
    results: list[ItemResult], loaded, cost: CostMeter, cache: Cache, consistency: dict[str, Any]
) -> dict[str, Any]:
    verdicts = Counter()
    by_lang: dict[str, Counter] = defaultdict(Counter)
    by_cat = Counter()
    skipped = Counter()
    for r in results:
        v = item_verdict(r.flags)["verdict"]
        verdicts[v] += 1
        by_lang[r.item.lang][v] += 1
        by_lang[r.item.lang]["items"] += 1
        for f in r.flags:
            by_cat[f.category] += 1
        for step, why in r.skipped.items():
            skipped[f"{step}: {why}"] += 1
    return {
        "items": len(results),
        "skipped_items": sum(p.fatal for p in loaded.problems),
        "filtered_out_by_lang": loaded.filtered_out,
        "problems": [vars(p) for p in loaded.problems],
        "verdicts": dict(verdicts),
        "by_lang": {k: dict(v) for k, v in sorted(by_lang.items())},
        "flags_by_category": dict(by_cat.most_common()),
        "skipped_steps": dict(skipped.most_common()),
        "judge_consistency": {k: v for k, v in consistency.items() if k != "per_item"},
        "cost": cost.summary(),
        "cache": {"hits": cache.hits, "misses": cache.misses},
    }


async def _run(args, registry, langs) -> int:
    loaded = load_dataset(args.dataset, registry, langs)
    for p in loaded.problems:
        event(
            log,
            "item_problem",
            logging.WARNING,
            line=p.line_no,
            item_id=p.item_id,
            reason=p.reason,
            skipped=p.fatal,
        )
    event(
        log,
        "dataset_loaded",
        items=len(loaded.items),
        filtered_out=loaded.filtered_out,
        skipped=sum(p.fatal for p in loaded.problems),
    )

    params = Params.defaults()
    if args.thresholds:
        params = params.with_overrides(
            thresholds_from_file(json.loads(args.thresholds.read_text("utf-8")))
        )
    human = load_human_labels(args.calibration, loaded) if args.calibration else {}
    runner = Runner(args, params, registry)
    runner.total = len(loaded.items)
    try:
        speech_ids = _speech_selected(loaded.items, args.speech_sample, args.seed)
        results = await asyncio.gather(
            *(runner.run_item(it, it.id in speech_ids) for it in loaded.items)
        )
        consistency = await runner.consistency(results)
        calibration = run_calibration(results, params, human, fixed=bool(args.thresholds))
        summary = summarize(results, loaded, runner.cost, runner.cache, consistency)
        if calibration is not None:
            summary["calibration"] = {
                k: calibration[k]
                for k in ("mode", "thresholds", "category_accuracy", "max_clean_flag_rate")
            } | {"metrics": calibration["metrics"]}
    finally:
        await runner.aclose()

    rows = [r.to_dict() for r in results]
    summary["quality"] = report.quality(rows)
    exit_code = 0
    diff = None
    if args.baseline:
        try:
            base_rows, _ = report.load_run(args.baseline)
        except (OSError, ValueError) as e:
            event(log, "baseline_unreadable", logging.ERROR, path=str(args.baseline), error=str(e))
        else:
            diff = report.compute_diff(rows, base_rows)
            summary["diff"] = report.public_diff(diff)
            delta = diff["quality"]["delta"]
            regressed = (
                args.fail_on_regression is not None
                and delta is not None
                and -delta > args.fail_on_regression
            )
            if regressed:
                exit_code = 3
                event(
                    log, "regression", logging.ERROR, delta=delta, allowed=args.fail_on_regression
                )

    with (args.out / "items.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    (args.out / "summary.md").write_text(
        report.render_summary(summary, rows, dataset=str(args.dataset)), encoding="utf-8"
    )
    if diff is not None:
        (args.out / "diff.md").write_text(
            report.render_diff(diff, str(args.baseline)), encoding="utf-8"
        )
    if calibration is not None:
        (args.out / "calibration.json").write_text(
            json.dumps(calibration, indent=2, ensure_ascii=False)
        )
    if consistency.get("ran"):
        (args.out / "judge_consistency.json").write_text(
            json.dumps(consistency, indent=2, ensure_ascii=False)
        )
    event(
        log,
        "run_done",
        items=len(results),
        verdicts=summary["verdicts"],
        cost_inr=summary["cost"]["total_inr"],
        quality=summary["quality"]["overall"],
    )
    return exit_code


def run(args) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    setup_logging(args.out, args.log_level)
    registry = load_languages()
    try:
        langs = resolve_langs(args.langs, registry)
    except ValueError as e:
        event(log, "bad_langs", logging.ERROR, error=str(e))
        return 2
    if not args.dataset.exists():
        event(log, "dataset_missing", logging.ERROR, path=str(args.dataset))
        return 2
    return asyncio.run(_run(args, registry, langs))
