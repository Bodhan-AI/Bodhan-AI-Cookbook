"""Run the harness end to end.

    load dataset (+ calibration labels) -> [--generate: translations, then planned speech]
    -> pre-pass: per-language statistics from the data itself
    -> per item, in a thread pool: translation checks, then speech checks (cheap -> costly)
    -> dataset-level checks -> thresholds (defaults -> --thresholds -> tuned on labels)
    -> fusion (signals -> flags) -> metrics -> report files

A bad item never stops the run. Every check call is wrapped: ``SkipCheck`` -> skipped(reason),
``ServiceUnavailable`` -> skipped(no key), ``BudgetExceeded`` -> skipped(budget), anything else ->
error(type: message). A check either completes or contributes no signals at all.

Timings and money actually spent go to ``logs/events.jsonl`` and ``run_meta.json``, never into the
report files, so a rerun from cache reproduces the report byte for byte.
"""

from __future__ import annotations

import json
import logging
import statistics
import subprocess
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import audio as A
from . import diff, report
from .budget import Budget, BudgetExceeded
from .cache import Cache
from .calibrate import cross_validated, evaluate, judge_consistency, per_check, tune
from .checks import DATASET_CHECKS, SPEECH_CHECKS, TRANSLATION_CHECKS, Check, DatasetStats, SkipCheck
from .checks.deterministic import length_log_ratio
from .checks.roundtrip import terms_to_transliterate, transliterable
from .checks.speech import spoken_chars
from .clients import BodhanClient, BodhanError, LLMClient, ServiceUnavailable
from .config import CATEGORIES, Settings, defaults
from .dataset import LoadReport, apply_calibration_file, load_dataset
from .fusion import fuse
from .generate import plan_speech, synthesize
from .models import Item, ItemResult

log = logging.getLogger("bodhan_eval.pipeline")

MAD_TO_SD = 1.4826  # scales a median absolute deviation to a normal standard deviation
AUDIO_ERRORS = (OSError, RuntimeError, ValueError, EOFError, wave.Error, subprocess.CalledProcessError)


@dataclass
class RunOptions:
    """What the caller (the CLI, or later ``bodhan_eval.api``) decided. No argparse types in here."""

    dataset: Path
    out: Path
    langs: set[str] | None = None  # None = every language in the dataset
    generate: bool = False
    baseline: Path | None = None
    fail_on_regression: bool = False  # CI mode: exit 3 when diff.md shows too many new problems
    max_new_major: int = 0
    calibration: Path | None = None
    max_cost_inr: float | None = None
    cache_dir: Path = Path(".cache/bodhan_eval")
    concurrency: int = 6
    thresholds: Path | None = None
    judge: bool = True
    judge_replicas: int | None = None
    judge_max_replicas: int | None = None
    judge_models: list[str] | None = None  # jury on the LLM endpoint; None = LLM_MODEL alone
    judge_repeat_sample: float | None = None
    speech: bool = True
    speech_sample: float | None = None
    voices: dict[str, str] = field(default_factory=dict)
    label_span_on: str = "translation"
    llm_inr_per_1k_tokens: float = 0.0
    llm_extra_body: dict[str, Any] | None = None  # merged into every LLM request (K1); None = provider defaults


class EventLog:
    """One JSON line per item x check (plus run-level events) in ``<out>/logs/events.jsonl``."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def write(self, **event: Any) -> None:
        line = json.dumps(report.clean(event), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._fh.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            self._fh.close()


@dataclass
class RunContext:
    """What checks may use (see ``checks/base.py``). Built once per run, shared by all worker threads."""

    params: dict[str, Any]
    judge_cfg: dict[str, Any]
    options: dict[str, Any]
    bodhan: BodhanClient
    llm: LLMClient
    budget: Budget
    cache: Cache
    events: EventLog
    stats: DatasetStats = field(default_factory=DatasetStats)
    transliterations: dict[tuple[str, str], str] = field(default_factory=dict)  # (lang, term) -> rendering

    def load_audio(self, path: str) -> tuple[np.ndarray, int]:
        return A.load_audio(path)


@dataclass
class RunResult:
    options: RunOptions
    results: list[ItemResult]  # dataset items, then calibration-only items, in load order
    load_report: LoadReport
    thresholds: dict[str, float]
    calibration: dict[str, Any]
    generation: dict[str, str]  # item key -> what --generate did for it
    budget: Budget
    llm_formats: dict[str, str] = field(default_factory=dict)  # judge model -> response format it ended up on


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(opts: RunOptions) -> int:
    """Run everything and write the report. Returns the process exit code."""
    started = time.monotonic()
    try:
        items, rep = _load(opts)
    except (OSError, UnicodeDecodeError) as e:
        log.error("cannot read dataset %s: %s", opts.dataset, e)
        return 1
    if not items:
        log.error("no valid items in %s for the requested languages (bad lines: %d)", opts.dataset, len(rep.bad))
        return 1
    opts.out.mkdir(parents=True, exist_ok=True)
    ctx = _context(opts)
    try:
        result = execute(items, rep, ctx, opts)
        records = report.write_all(opts.out, result)
        counts = diff.write_diff(opts.out, opts.baseline, records) if opts.baseline is not None else None
        report.write_run_meta(opts.out, result, runtime_s=time.monotonic() - started)
    finally:
        ctx.events.close()
        ctx.cache.close()
    log.info("report written to %s (spent ₹%.4f this run)", opts.out, ctx.budget.total_spent)
    if opts.fail_on_regression and counts is not None and diff.gate_exit_code(counts, opts.max_new_major):
        log.error("regression gate: %d new major or critical problems (allowed %d); see diff.md",
                  counts["new_major_or_worse"], opts.max_new_major)  # fmt: skip
        return 3
    return 0


def execute(items: list[Item], rep: LoadReport, ctx: RunContext, opts: RunOptions) -> RunResult:
    """Everything between loading and writing; separate from ``run`` so a library caller can reuse it."""
    generation = _generate(items, ctx, opts) if opts.generate else {}
    ctx.stats = prepass_stats(items, ctx)
    prefetch_transliterations(items, ctx, opts.concurrency)
    results = [ItemResult(item=it) for it in items]
    checks: list[Check] = [cls() for cls in TRANSLATION_CHECKS]
    if opts.speech:
        checks += [cls() for cls in SPEECH_CHECKS]
    _run_items(results, checks, ctx, opts.concurrency)
    for dataset_check in DATASET_CHECKS:
        dataset_check(results)
    thresholds, calibration = _thresholds(results, opts)
    _fuse_all(results, thresholds, ctx)
    calibration.update(
        evaluation=evaluate(results),
        per_check=per_check(results),
        judge_consistency=judge_consistency(results),
    )
    return RunResult(opts, results, rep, thresholds, calibration, generation, ctx.budget, ctx.llm.formats_used())


# ---------------------------------------------------------------------------
# setup and loading
# ---------------------------------------------------------------------------


def _load(opts: RunOptions) -> tuple[list[Item], LoadReport]:
    items, rep = load_dataset(opts.dataset, opts.langs, opts.label_span_on)
    if opts.calibration is not None:
        try:
            items += apply_calibration_file(opts.calibration, items, opts.langs, rep, opts.label_span_on)
        except (OSError, UnicodeDecodeError) as e:
            log.warning("cannot read calibration file %s: %s", opts.calibration, e)
            rep.notes.append({"item": "--calibration", "reason": f"file unreadable: {type(e).__name__}"})
    return items, rep


def _context(opts: RunOptions) -> RunContext:
    settings = Settings.from_env()
    settings.concurrency = opts.concurrency
    cache = Cache(opts.cache_dir)
    budget = Budget(opts.max_cost_inr)
    judge_cfg = dict(defaults()["judge"])
    for key, value in (
        ("replicas", opts.judge_replicas),
        ("max_replicas", opts.judge_max_replicas),
        ("models", opts.judge_models),
        ("repeat_sample", opts.judge_repeat_sample),
    ):
        if value is not None:
            judge_cfg[key] = value
    if opts.judge_models and not settings.llm_model:
        settings.llm_model = opts.judge_models[0]  # a jury needs no LLM_MODEL of its own
    return RunContext(
        params=dict(defaults()["check_params"]),
        judge_cfg=judge_cfg,
        options={"judge": opts.judge, "voices": dict(opts.voices)},
        bodhan=BodhanClient(settings, cache, budget),
        llm=LLMClient(settings, cache, budget, opts.llm_inr_per_1k_tokens, opts.llm_extra_body),
        budget=budget,
        cache=cache,
        events=EventLog(opts.out / "logs" / "events.jsonl"),
    )


# ---------------------------------------------------------------------------
# --generate
# ---------------------------------------------------------------------------


def _generate(items: list[Item], ctx: RunContext, opts: RunOptions) -> dict[str, str]:
    """Fill missing translations, then synthesise planned audio. Returns a decision per item key."""
    decisions: dict[str, str] = {}
    missing = [it for it in items if not it.translation]
    with ThreadPoolExecutor(max_workers=opts.concurrency) as pool:
        for key, what in pool.map(lambda it: _translate_one(it, ctx), missing):
            decisions[key] = what
    if opts.speech:
        chosen, speech_decisions = plan_speech(items, opts.speech_sample, ctx.budget.remaining())
        audio_dir = opts.out / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=opts.concurrency) as pool:
            for key, failure in pool.map(lambda it: _synthesize_one(it, ctx, audio_dir), chosen):
                if failure:
                    speech_decisions[key] = failure
        for key, what in speech_decisions.items():
            decisions[key] = f"{decisions[key]}; {what}" if key in decisions else what
    _write_generated(items, opts.out)
    return decisions


def _translate_one(it: Item, ctx: RunContext) -> tuple[str, str]:
    try:
        res = ctx.bodhan.translate(it.source, it.lang)
    except (ServiceUnavailable, BudgetExceeded, BodhanError) as e:
        return it.key, f"no translation: {e}"
    if not res["translation"].strip():
        return it.key, "no translation: Indic-Translate returned empty text"
    it.translation = res["translation"]
    it.generated.append("translation")
    return it.key, "translation generated" + (" (truncated by the API)" if res["truncated"] else "")


def _synthesize_one(it: Item, ctx: RunContext, audio_dir: Path) -> tuple[str, str | None]:
    try:
        it.audio = synthesize(it, ctx, audio_dir)
    except (ServiceUnavailable, BudgetExceeded, BodhanError, ValueError) as e:
        return it.key, f"no audio: {e}"
    it.generated.append("audio")
    return it.key, None


def _write_generated(items: list[Item], out: Path) -> None:
    """``<out>/generated.jsonl``: the outputs we created, with audio paths relative to ``out``."""
    rows = []
    for it in items:
        if not it.generated:
            continue
        audio = None
        if "audio" in it.generated and it.audio:
            audio = Path(it.audio).resolve().relative_to(out.resolve()).as_posix()
        rows.append({"id": it.id, "lang": it.lang, "source": it.source, "translation": it.translation, "audio": audio})
    report.write_jsonl(out / "generated.jsonl", rows)


# ---------------------------------------------------------------------------
# pre-pass statistics
# ---------------------------------------------------------------------------


def prepass_stats(items: list[Item], ctx: RunContext) -> DatasetStats:
    """Per-language length ratio and speaking rate, estimated from this dataset (nothing hand-tuned per language).

    Robust estimates (median, MAD) so a few broken items do not move the reference they are judged against.
    Languages with too few items fall back to the priors in ``langs.json`` inside the checks.
    """
    p = ctx.params
    stats = DatasetStats()
    ratios: dict[str, list[float]] = {}
    for it in items:
        lr = length_log_ratio(it.source, it.translation) if it.translation else None
        if lr is not None:
            ratios.setdefault(it.lang, []).append(lr)
    for lang, vals in sorted(ratios.items()):
        if len(vals) >= p["length_min_items_per_lang"]:
            med = statistics.median(vals)
            mad = statistics.median(abs(v - med) for v in vals)
            stats.length_ratio[lang] = (med, MAD_TO_SD * mad, len(vals))
    rates: dict[str, list[float]] = {}
    for it in items:
        rate = _speaking_rate(it, ctx) if it.audio and it.translation else None
        if rate is not None:
            rates.setdefault(it.lang, []).append(rate)
    for lang, vals in sorted(rates.items()):
        if len(vals) >= p["rate_min_items_per_lang"]:
            stats.chars_per_second[lang] = (statistics.median(vals), len(vals))
    return stats


def prefetch_transliterations(items: list[Item], ctx: RunContext, concurrency: int) -> None:
    """Transliterate each unique (language, source term) once, before the per-item checks (K6).

    Items that share a name would otherwise race to pay for the same call before the cache has it.
    A failed call is stored as "" so the entity check falls back to the back-translation without retrying.
    """
    if not ctx.bodhan.available("transliterate"):
        return
    wanted = sorted(
        {
            (it.lang, term)
            for it in items
            if it.translation and transliterable(it.lang)
            for term in terms_to_transliterate(it.source, it.translation)
        }
    )

    def fetch(key: tuple[str, str]) -> tuple[tuple[str, str], str]:
        lang, term = key
        try:
            return key, ctx.bodhan.transliterate(term, lang)
        except (ServiceUnavailable, BudgetExceeded, BodhanError) as e:
            log.info("transliteration of %r (%s) unavailable: %s", term, lang, e)
            return key, ""

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for key, value in pool.map(fetch, wanted):
            ctx.transliterations[key] = value


def _speaking_rate(it: Item, ctx: RunContext) -> float | None:
    """Spoken characters per second of active speech, or None if the audio cannot be measured."""
    try:
        x, sr = ctx.load_audio(it.audio or "")
    except AUDIO_ERRORS as e:
        log.info("%s: audio not readable for the speaking-rate pre-pass (%s)", it.key, e)
        return None
    active = A.analyse(x, sr).active_s
    if active < 0.5:
        return None
    return spoken_chars(it.translation or "") / active


# ---------------------------------------------------------------------------
# per-item checks
# ---------------------------------------------------------------------------


def _run_items(results: list[ItemResult], checks: list[Check], ctx: RunContext, concurrency: int) -> None:
    done = 0
    lock = threading.Lock()
    step = max(1, len(results) // 10)

    def work(r: ItemResult) -> None:
        nonlocal done
        run_item(r, checks, ctx)
        with lock:
            done += 1
            if done % step == 0 or done == len(results):
                log.info("checked %d/%d items", done, len(results))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(work, results))


def run_item(r: ItemResult, checks: list[Check], ctx: RunContext) -> None:
    """Run every applicable check on one item, in order. Checks of one item run sequentially in one thread."""
    if not r.item.translation and not r.item.audio:
        r.status = "skipped"
        r.reason = "nothing to score: no translation or audio (run with --generate to create them)"
        ctx.events.write(item=r.item.key, check=None, status=r.status, reason=r.reason)
        return
    for check in checks:
        r.checks[check.name] = run_check(check, r, ctx)


def run_check(check: Check, r: ItemResult, ctx: RunContext) -> dict[str, Any]:
    before, hits_before, t0 = len(r.signals), ctx.budget.thread_cache_hits(), time.perf_counter()
    status, reason = "ok", None
    try:
        if not check.applies(r, ctx):
            return {"status": "not_applicable"}
        check.run(r, ctx)
    except SkipCheck as e:
        status, reason = "skipped", str(e)
    except ServiceUnavailable as e:
        status, reason = "skipped", f"no key: {e}"
    except BudgetExceeded as e:
        status, reason = "skipped", f"budget: {e}"
    except Exception as e:  # a bad item or a checker bug must never stop the run; recorded and logged
        status, reason = "error", f"{type(e).__name__}: {e}"
        log.warning("%s: check %s failed: %s", r.item.key, check.name, reason)
    if status != "ok":
        del r.signals[before:]  # a check either completes or contributes nothing
    new = r.signals[before:]
    ctx.events.write(
        item=r.item.key,
        check=check.name,
        status=status,
        reason=reason,
        ms=round(1000 * (time.perf_counter() - t0), 1),
        cache_hits=ctx.budget.thread_cache_hits() - hits_before,
        signals=[s.to_dict() for s in new],
    )
    out: dict[str, Any] = {"status": status, "signals": len(new)}
    if reason:
        out["reason"] = reason
    return out


# ---------------------------------------------------------------------------
# thresholds, calibration and fusion
# ---------------------------------------------------------------------------


def _thresholds(results: list[ItemResult], opts: RunOptions) -> tuple[dict[str, float], dict[str, Any]]:
    """defaults.json -> --thresholds file -> per-category tuning on labelled items (if any)."""
    d = defaults()
    base = dict(d["flag_threshold"])
    source = "defaults"
    if opts.thresholds is not None:
        base.update(load_threshold_file(opts.thresholds))
        source = "defaults + --thresholds file"
    grid, min_labels, margin = d["threshold_grid"], d["min_labels_to_tune"], d["uncertain_margin"]
    cv = cross_validated(results, base, grid, min_labels, margin)  # scored on held-out halves: not optimistic
    tuned, tuning = tune(results, base, grid, min_labels, margin)
    return tuned, {"threshold_source": source, "base_thresholds": base, "tuning": tuning, "cross_validated": cv}


def load_threshold_file(path: Path) -> dict[str, float]:
    """``{"category": threshold}``; unknown categories and non-numbers are ignored with a warning."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for cat, value in raw.items() if isinstance(raw, dict) else []:
        if cat in CATEGORIES and isinstance(value, (int, float)) and 0.0 <= value <= 1.0:
            out[cat] = float(value)
        else:
            log.warning("--thresholds: ignoring %r: %r", cat, value)
    return out


def _fuse_all(results: list[ItemResult], thresholds: dict[str, float], ctx: RunContext) -> None:
    margin = defaults()["uncertain_margin"]
    for r in results:
        r.flags, r.uncertain, below = fuse(r.item, r.signals, thresholds, margin)
        for f in below:
            ctx.events.write(item=r.item.key, check="fusion", status="below_threshold", flag=f.to_dict())
