"""The orchestrator: dataset -> cheap deterministic checks -> round-trip -> judge -> triage ->
speech -> combiner -> ItemResults. Cheapest work first, paid speech last, budget enforced.

Everything is async with a global concurrency limit. A failure on one item is logged, counted
and skipped; it never stops the run.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from pathlib import Path

from .cache import canonical_key
from .checks import all_checks
from .checks.registry import CheckContext
from .clients.base import BodhanClients
from .combiner import combine, finalize_item
from .config import LanguageConfig, Thresholds, load_languages, load_thresholds
from .cost import BudgetExceeded, CostLedger
from .dataset import LoadReport, load_dataset
from .judge import JudgeClient, judge_to_evidence
from .logging_utils import StructuredLogger
from .models import Evidence, Item, ItemResult
from .speech import analyze_signal, asr_evidence, signal_evidence, transcript_is_degenerate
from .triage import select_for_asr


@dataclass
class RunConfig:
    dataset: str
    langs: list[str]
    out: str
    generate: bool = False
    baseline: str | None = None
    calibration: str | None = None
    max_cost_inr: float | None = None
    judge_input_inr_per_million: float | None = None
    judge_output_inr_per_million: float | None = None
    concurrency: int = 8
    cache_dir: str = ".cache"
    speech_sample_rate: float = 0.25
    judge_repeats: int = 1
    no_judge: bool = False
    no_speech: bool = False
    seed: int = 0
    log_level: str = "info"


@dataclass
class RunState:
    results: dict[str, ItemResult] = field(default_factory=dict)
    load_report: LoadReport | None = None
    ledger: CostLedger | None = None
    judge_consistency: dict = field(default_factory=dict)
    judge_usage: dict = field(default_factory=dict)
    judge_priced: bool = False
    skipped_for_budget: list[str] = field(default_factory=list)


async def run_pipeline(cfg: RunConfig, clients: BodhanClients, judge: JudgeClient | None,
                       cache, logger: StructuredLogger, thresholds: Thresholds | None = None,
                       languages: LanguageConfig | None = None) -> RunState:
    languages = languages or load_languages()
    thresholds = thresholds or load_thresholds()
    ledger = getattr(clients.translate, "ledger", None) or CostLedger(cfg.max_cost_inr)
    load = load_dataset(cfg.dataset, cfg.langs)
    logger.info("dataset_loaded", **load.counts())
    state = RunState(load_report=load, ledger=ledger)

    items = sorted(load.items, key=lambda it: it.id)
    for it in items:
        state.results[it.id] = ItemResult(id=it.id, lang=it.lang)

    # ---- Phase A: translation stages (generation, round-trip, checks, judge) ----
    evidence_by_item: dict[str, list[Evidence]] = {it.id: [] for it in items}
    judge_verdicts: dict[str, str | None] = {it.id: None for it in items}

    async def phase_a(it: Item) -> None:
        res = state.results[it.id]
        try:
            if it.translation is None and cfg.generate:
                await _generate_translation(it, clients, logger, state)
            if it.translation is None:
                if it.audio and it.reference and not cfg.no_speech:
                    res.details["translation_check"] = "unavailable"
                    return
                res.status = "skipped"
                res.skip_reason = "no_translation"
                return
            artifacts: dict = {}
            # Stage 4: round-trip back-translation (cheap, noisy)
            await _back_translate(it, clients, artifacts, logger, state)
            # Stage 2/3: deterministic checks
            ctx = CheckContext(item=it, lang=it.lang, thresholds=thresholds,
                               languages=languages, artifacts=artifacts)
            for spec in all_checks():
                try:
                    evs = spec.fn(ctx)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("check_error", item=it.id, check=spec.name, error=str(exc))
                    res.details.setdefault("check_errors", {})[spec.name] = str(exc)
                    continue
                evidence_by_item[it.id].extend(evs)
                res.checks_run.append(spec.name)
            # round-trip evidence from artifacts
            evidence_by_item[it.id].extend(_roundtrip_evidence(it, artifacts, thresholds))
            # Stage 5: judge
            if judge is not None and not cfg.no_judge:
                await _run_judge(it, judge, cfg, languages, evidence_by_item, judge_verdicts,
                                 state, logger)
            elif not cfg.no_judge:
                res.details["judge_error"] = "judge_not_configured"
        except Exception as exc:  # noqa: BLE001
            logger.error("item_error", item=it.id, stage="A", error=str(exc))
            res.status = "skipped"
            res.skip_reason = f"error: {exc}"

    await _gather(items, phase_a, cfg.concurrency)

    # ---- Phase B: triage for speech ----
    speech_ids: set[str] = set()
    if not cfg.no_speech:
        suspicious = {
            it.id for it in items
            if evidence_by_item[it.id] and any(
                (not e.noisy) or e.check == "judge" for e in evidence_by_item[it.id])
        }
        supplied = [it for it in items if it.audio and Path(_audio_path(cfg, it)).is_file()]
        for it in items:
            if it.audio and not Path(_audio_path(cfg, it)).is_file() and not cfg.generate:
                state.results[it.id].details["speech_check_error"] = "audio_file_missing"
        generated_candidates = [it for it in items if cfg.generate and it.translation
                                and it not in supplied]
        audio_items = supplied + generated_candidates
        max_asr = None
        speech_ids = select_for_asr(audio_items, suspicious & {a.id for a in audio_items},
                                    cfg.speech_sample_rate, seed=cfg.seed, max_items=max_asr)
        # Existing outputs are the evaluation target, not a sample to be synthesized.
        speech_ids.update(it.id for it in supplied)
        logger.info("triage", speech_candidates=len(audio_items), selected=len(speech_ids))

    # ---- Phase C: speech checks ----
    async def phase_c(it: Item) -> None:
        if it.id not in speech_ids:
            return
        try:
            await _speech_checks(it, cfg, clients, thresholds, evidence_by_item, logger, state, cache)
            logger.info("speech_check_completed", item=it.id,
                        error=state.results[it.id].details.get("speech_check_error"))
        except BudgetExceeded:
            state.skipped_for_budget.append(it.id)
            state.results[it.id].details["speech_check_error"] = "budget_exceeded"
            logger.warning("speech_skipped_budget", item=it.id)
        except Exception as exc:  # noqa: BLE001
            state.results[it.id].details["speech_check_error"] = str(exc)
            logger.error("item_error", item=it.id, stage="C", error=str(exc))

    await _gather([it for it in items if it.id in speech_ids], phase_c, cfg.concurrency)

    # ---- combine ----
    for it in items:
        res = state.results[it.id]
        if res.status == "skipped":
            continue
        res.evidence = evidence_by_item[it.id]
        res.details["judge_verdict"] = judge_verdicts[it.id]
        outcome = combine(res.evidence, thresholds, it.lang, judge_verdicts[it.id])
        finalize_item(res, outcome)
        if res.status == "clean" and any(res.details.get(k) for k in
                                         ("speech_check_error", "check_errors",
                                          "backtranslation_error", "judge_error")):
            res.status = "uncertain"

    state.ledger = ledger
    state.judge_usage = dict(getattr(judge, "usage", {})) if judge is not None else {}
    state.judge_priced = bool(judge is not None and getattr(judge, "input_rate", None) is not None
                              and getattr(judge, "output_rate", None) is not None)
    return state


# ---- stage helpers --------------------------------------------------------------------


async def _generate_translation(it: Item, clients: BodhanClients, logger, state) -> None:
    try:
        r = await clients.translate.translate(it.source, "en", it.lang)
        it.translation = r.text
        state.results[it.id].cost_inr += r.logical_cost_inr
    except BudgetExceeded:
        state.skipped_for_budget.append(it.id)
        logger.warning("generate_skipped_budget", item=it.id)


async def _back_translate(it: Item, clients: BodhanClients, artifacts: dict, logger, state) -> None:
    try:
        r = await clients.back_translate(it.translation or "", it.lang)
        artifacts["back_translation"] = r.text
        state.results[it.id].cost_inr += r.logical_cost_inr
    except BudgetExceeded:
        state.results[it.id].details["backtranslation_error"] = "budget_exceeded"
        logger.warning("backtranslate_skipped_budget", item=it.id)
    except Exception as exc:  # noqa: BLE001
        state.results[it.id].details["backtranslation_error"] = str(exc)
        logger.warning("backtranslate_error", item=it.id, error=str(exc))


def _roundtrip_evidence(it: Item, artifacts: dict, thresholds: Thresholds) -> list[Evidence]:
    back = artifacts.get("back_translation")
    if not isinstance(back, str) or not back.strip() or not it.source.strip():
        return []
    from .normalize import norm_for_asr
    from .text_utils import chrf, token_overlap

    try:
        score = chrf(back, it.source)
    except Exception:  # noqa: BLE001
        return []
    overlap = token_overlap(norm_for_asr(back, "en"), norm_for_asr(it.source, "en"))
    cut = thresholds.get("backtranslation_chrf_cut", it.lang)
    if score >= cut:
        return []
    return [Evidence(
        check="roundtrip", category="mistranslation", strength="medium", noisy=True,
        score=(cut - score) / max(cut, 1.0), severity_hint="major",
        details={"back_translation_chrf": round(score, 2), "token_overlap": round(overlap, 3),
                 "cut": cut, "back_translation": back[:300]},
    )]


async def _run_judge(it, judge, cfg, languages, evidence_by_item, judge_verdicts, state, logger):
    lang_spec = languages.get(it.lang)
    lang_name = lang_spec.name if lang_spec else it.lang
    verdicts = []
    cats = []
    first_result = None
    for rep in range(max(1, cfg.judge_repeats)):
        try:
            jr = await judge.judge(it, lang_name, repeat_index=rep)
        except Exception as exc:  # noqa: BLE001
            logger.warning("judge_error", item=it.id, error=str(exc))
            state.results[it.id].details["judge_error"] = str(exc)
            continue
        if not jr.parse_ok:
            state.results[it.id].details["judge_error"] = "invalid_judge_response"
            continue
        verdicts.append(jr.verdict)
        cats.append(tuple(sorted({e.category for e in jr.errors})))
        if rep == 0:
            first_result = jr
    if first_result is not None:
        evidence_by_item[it.id].extend(judge_to_evidence(first_result, it))
        judge_verdicts[it.id] = first_result.verdict
    if len(verdicts) > 1:
        agree_v = verdicts.count(max(set(verdicts), key=verdicts.count)) / len(verdicts)
        agree_c = cats.count(max(set(cats), key=cats.count)) / len(cats)
        state.judge_consistency[it.id] = {"verdict_agreement": agree_v,
                                          "category_agreement": agree_c, "lang": it.lang}


async def _speech_checks(it, cfg, clients, thresholds, evidence_by_item, logger, state, cache):
    audio_path = _audio_path(cfg, it)
    if (not audio_path or not Path(audio_path).is_file()) and cfg.generate and it.translation:
        r = await clients.speak.speak(it.translation, it.lang)
        audio_path = r.audio_path
        state.results[it.id].cost_inr += r.logical_cost_inr
    if not audio_path or not Path(audio_path).is_file():
        return
    spoken = it.translation or it.reference or ""
    feat = analyze_signal(audio_path)
    if not feat.ok:
        raise ValueError(f"audio analysis failed: {feat.error}")
    state.results[it.id].details["signal"] = {
        "duration_s": round(feat.duration_s, 2), "ok": feat.ok,
        "rms": feat.rms_downsampled,
    }
    evidence_by_item[it.id].extend(signal_evidence(it.lang, spoken, feat, thresholds))
    state.results[it.id].checks_run.extend(["s1_duration", "s2_silence", "s3_clipping", "s4_audio_loop"])
    # paid ASR
    r = await clients.transcribe.transcribe(audio_path, it.lang)
    state.results[it.id].cost_inr += r.logical_cost_inr
    if transcript_is_degenerate(r.text):
        transcript = await _recover_transcript(audio_path, it, clients, cache, state, segments=2)
        if transcript is None:
            state.results[it.id].details["speech_check_error"] = "recognizer_output_degenerate"
            return
        state.results[it.id].details["speech_checker_recovered"] = True
    else:
        transcript = r.text
    asr_signals = asr_evidence(it.lang, spoken, transcript, feat, thresholds)
    evidence_by_item[it.id].extend(asr_signals)
    omissions = [ev for ev in asr_signals if ev.category == "speech_omission"]
    if omissions:
        # An independently segmented pass can corroborate the structure of a deletion.
        # It is still the same recognizer and must not be treated as ground truth.
        segments = 3 if state.results[it.id].details.get("speech_checker_recovered") else 2
        try:
            alternative = await _recover_transcript(audio_path, it, clients, cache, state, segments=segments)
            if alternative is not None:
                other = asr_evidence(it.lang, spoken, alternative, feat, thresholds)
                if any(ev.category == "speech_omission"
                       and ev.details.get("pattern") == omissions[0].details.get("pattern") for ev in other):
                    evidence_by_item[it.id].append(Evidence(
                        check="s6_segmented_consensus", category="speech_omission",
                        strength="medium", noisy=True, severity_hint="major",
                        details={"pattern": omissions[0].details.get("pattern"),
                                 "segments": segments, "recognizer": "same_model"},
                    ))
                state.results[it.id].checks_run.append("s6_segmented_consensus")
        except Exception as exc:  # noqa: BLE001
            state.results[it.id].details["speech_recheck_error"] = str(exc)
            logger.warning("speech_recheck_error", item=it.id, error=str(exc))
    state.results[it.id].checks_run.extend(["s5_repetition", "s6_omission", "s7_garbled"])


async def _recover_transcript(audio_path, it, clients, cache, state, segments: int = 2) -> str | None:
    """Transcribe consecutive non-overlapping audio segments, rejecting degenerate outputs."""
    import soundfile as sf

    audio, rate = sf.read(audio_path, dtype="float32", always_2d=True)
    if len(audio) < rate * segments:
        return None
    outputs = []
    boundaries = [len(audio) * index // segments for index in range(segments + 1)]
    for index in range(segments):
        segment = audio[boundaries[index]:boundaries[index + 1]]
        buffer = io.BytesIO()
        sf.write(buffer, segment, rate, format="WAV", subtype="PCM_16")
        data = buffer.getvalue()
        key = canonical_key("audio", "recognizer_recovery_v1", {"lang": it.lang}, data)
        path = cache.put_bytes(key, data, ".wav")
        result = await clients.transcribe.transcribe(str(path), it.lang)
        state.results[it.id].cost_inr += result.logical_cost_inr
        if transcript_is_degenerate(result.text):
            return None
        outputs.append(result.text.strip())
    text = " ".join(part for part in outputs if part)
    return text or None


def _audio_path(cfg: RunConfig, it: Item) -> str:
    if not it.audio:
        return ""
    p = Path(it.audio)
    if p.is_absolute():
        return str(p)
    # resolve relative to the dataset directory
    return str(Path(cfg.dataset).resolve().parent / p)


async def _gather(items, fn, concurrency: int) -> None:
    limiter = asyncio.Semaphore(max(1, concurrency))

    async def wrap(it):
        async with limiter:
            await fn(it)

    await asyncio.gather(*(wrap(it) for it in items))
