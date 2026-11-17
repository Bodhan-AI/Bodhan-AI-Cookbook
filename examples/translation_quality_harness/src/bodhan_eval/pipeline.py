"""Orchestration: cheap checks -> round trips -> dataset statistics -> judge -> fusion.

Phases run over all items with bounded concurrency. Any exception is contained to its item
(recorded as `failed`) or its check (recorded as `error`); the run always produces a report.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import log
from .audio.analysis import analyze
from .audio.io import AudioDecodeError, encode_wav, load
from .budget import BudgetExceeded
from .cache import atomic_write_bytes
from .checks.base import ItemContext, skip
from .checks.numbers import check_numbers
from .checks.roundtrip import check_backtranslation, check_latin_terms
from .checks.script import check_script
from .checks.speech import SpeechRateModel, check_audio_signal, check_transcription
from .checks.textual import LengthModel, check_format, check_length, check_reference, length_features
from .clients.bodhan import SPEAK, TRANSCRIBE, TRANSLATE, BodhanClient
from .clients.http import ServiceError
from .config import Thresholds
from .dataset import LoadedItem
from .fusion import fuse, mqm
from .generate import pick_voice, synthesize
from .judge.judge import Judge, JudgeOutcome, group_candidates
from .languages import get_language
from .models import CheckResult, ItemResult, Severity
from .text.speakable import to_speakable


@dataclass
class RunOptions:
    out_dir: Path
    generate: bool = False
    speech: str = "auto"  # auto | off
    speech_sample: float = 1.0
    judge_policy: str = "all"  # all | suspicious | off
    judge_votes: int = 1
    judge_repeats: int = 0
    judge_repeat_sample: int = 40
    voice: str | None = None
    item_concurrency: int = 24


@dataclass
class RunState:
    results: list[ItemResult]
    contexts: dict[str, ItemContext]
    consistency: dict | None = None
    warnings: list[str] = field(default_factory=list)
    outcomes: dict[str, JudgeOutcome] = field(default_factory=dict)


def _sampled(uid: str, fraction: float) -> bool:
    if fraction >= 1.0:
        return True
    digest = int(hashlib.sha256(uid.encode()).hexdigest()[:8], 16)
    return digest / 0xFFFFFFFF < fraction


class Pipeline:
    def __init__(self, thresholds: Thresholds, bodhan: BodhanClient, judge: Judge | None, opts: RunOptions):
        self.th = thresholds
        self.bodhan = bodhan
        self.judge = judge
        self.opts = opts
        self.warnings: list[str] = []
        self._item_sem = asyncio.Semaphore(opts.item_concurrency)

    async def _each(self, ctxs: list[ItemContext], fn) -> None:
        async def guarded(ctx: ItemContext):
            if ctx.failed:
                return
            async with self._item_sem:
                try:
                    await fn(ctx)
                except Exception as exc:  # noqa: BLE001 - isolate item failures
                    ctx.failed = f"{fn.__name__}: {type(exc).__name__}: {exc}"
                    log.warn("item_failed", item=ctx.item.id, lang=ctx.lang.code, stage=fn.__name__, error=str(exc)[:300])

        await asyncio.gather(*(guarded(c) for c in ctxs))

    async def run(self, loaded: list[LoadedItem]) -> RunState:
        ctxs = [
            ItemContext(item=li.item, lang=get_language(li.item.lang), uid=li.uid, audio_path=str(li.audio_path) if li.audio_path else None) for li in loaded
        ]

        log.info("phase", stage="prepare", count=len(ctxs))
        await self._each(ctxs, self._prepare)
        log.info("phase", stage="roundtrip", count=len(ctxs))
        await self._each(ctxs, self._roundtrip)

        length_model = LengthModel(self._length_ratios(ctxs))
        rate_model = SpeechRateModel(self._speech_rates(ctxs))
        log.info("phase", stage="statistics", count=len(ctxs))

        async def stats_checks(ctx: ItemContext):
            if ctx.translation:
                check_length(ctx, length_model, self.th)
            if ctx.audio is not None:
                check_audio_signal(ctx, self.th, rate_model)

        await self._each(ctxs, stats_checks)

        log.info("phase", stage="judge", count=len(ctxs))
        outcomes: dict[str, JudgeOutcome] = {}

        async def judge_item(ctx: ItemContext):
            outcome = await self._judge(ctx)
            if outcome is not None:
                outcomes[ctx.uid] = outcome

        await self._each(ctxs, judge_item)
        consistency = await self._consistency(ctxs, outcomes)

        results = [self._result(ctx, outcomes.get(ctx.uid)) for ctx in ctxs]
        return RunState(results, {c.uid: c for c in ctxs}, consistency, self.warnings, outcomes)

    async def _prepare(self, ctx: ItemContext) -> None:
        item = ctx.item
        if not ctx.lang.known:
            ctx.warnings.append(f"language {item.lang!r} is not in the registry; script checks are limited")
        ctx.translation = item.translation
        if ctx.translation is None and self.opts.generate:
            try:
                result = await self.bodhan.translate(item.source, item.lang)
                ctx.translation = result.value["translation"]
                if result.value.get("truncated"):
                    ctx.warnings.append("generated translation was truncated by max_output_tokens")
            except (ServiceError, BudgetExceeded) as exc:
                ctx.warnings.append(f"translation not generated: {exc}")
        if ctx.translation is None:
            skip(ctx, "translation_checks", "deterministic", "no translation supplied (use --generate to create one)")
        else:
            check_script(ctx, self.th)
            check_format(ctx)
            check_latin_terms(ctx)
            check_reference(ctx)
            ctx.speakable = to_speakable(ctx.translation)
        await self._load_audio(ctx)

    async def _load_audio(self, ctx: ItemContext) -> None:
        if ctx.audio_path:
            path = Path(ctx.audio_path)
            if not path.is_file():
                ctx.warnings.append(f"audio file not found: {ctx.item.audio}")
                skip(ctx, "audio_signal", "deterministic", "audio file not found")
                ctx.audio_path = None
            else:
                try:
                    ctx.audio = await asyncio.to_thread(load, path)
                except (AudioDecodeError, OSError) as exc:
                    ctx.warnings.append(f"audio could not be decoded: {exc}")
                    skip(ctx, "audio_signal", "deterministic", "audio could not be decoded")
                    ctx.audio_path = None
        elif self.opts.generate and self.opts.speech != "off" and ctx.speakable and ctx.speakable.text:
            if not _sampled(ctx.uid, self.opts.speech_sample):
                skip(ctx, "speech_generation", "generate", f"not in the {self.opts.speech_sample:.0%} speech sample")
            elif not self.bodhan.available(SPEAK):
                skip(ctx, "speech_generation", "generate", f"indic-speak unavailable: {self.bodhan.disabled.get(SPEAK)}")
            else:
                try:
                    speech = await synthesize(self.bodhan, ctx.speakable, ctx.lang.code, pick_voice(ctx.lang.code, self.opts.voice))
                    ctx.audio = speech.audio
                    out = self.opts.out_dir / "audio" / f"{_safe(ctx.uid)}.wav"
                    await asyncio.to_thread(atomic_write_bytes, out, encode_wav(speech.audio))
                    ctx.audio_path = str(out)
                    ctx.checks.append(
                        CheckResult(
                            name="speech_generation",
                            stage="generate",
                            metrics={"chunks": len(speech.chunks), "voice": pick_voice(ctx.lang.code, self.opts.voice)},
                            cache="hit" if speech.cached else "miss",
                        )
                    )
                except (ServiceError, BudgetExceeded, ValueError, AudioDecodeError) as exc:
                    ctx.warnings.append(f"speech not generated: {exc}")
                    skip(ctx, "speech_generation", "generate", str(exc)[:200])
        if ctx.audio is not None:
            ctx.audio_stats = await asyncio.to_thread(analyze, ctx.audio, noise_flatness=self.th.noise_flatness, min_noise_s=self.th.min_noise_s)

    def _whole_text_failure(self, ctx: ItemContext) -> bool:
        n = len(ctx.translation or "")
        return any(s.hard and s.severity == Severity.CRITICAL and s.span == (0, n) for s in ctx.signals)

    async def _roundtrip(self, ctx: ItemContext) -> None:
        tasks = []
        if ctx.translation and not self._whole_text_failure(ctx):
            tasks.append(self._back_translate(ctx))
        elif ctx.translation:
            skip(ctx, "back_translation", "roundtrip", "skipped: cheap checks already found a whole-text failure")
        if ctx.audio is not None and ctx.speakable is not None:
            tasks.append(self._transcribe(ctx))
        await asyncio.gather(*tasks)
        if ctx.translation:
            check_numbers(ctx)
            if ctx.back_translation:
                check_backtranslation(ctx, self.th)
        if ctx.transcript is not None:
            check_transcription(ctx, self.th)

    async def _back_translate(self, ctx: ItemContext) -> None:
        if not self.bodhan.available(TRANSLATE):
            skip(ctx, "back_translation", "roundtrip", f"indic-translate unavailable: {self.bodhan.disabled.get(TRANSLATE)}")
            return
        try:
            result = await self.bodhan.translate(ctx.translation or "", "en")
            ctx.back_translation = result.value["translation"]
        except (ServiceError, BudgetExceeded) as exc:
            ctx.warnings.append(f"back-translation failed: {exc}")
            skip(ctx, "back_translation", "roundtrip", str(exc)[:200])

    async def _transcribe(self, ctx: ItemContext) -> None:
        if not self.bodhan.available(TRANSCRIBE):
            skip(ctx, "back_transcription", "roundtrip", f"indic-transcribe unavailable: {self.bodhan.disabled.get(TRANSCRIBE)}")
            return
        try:
            result = await self.bodhan.transcribe(ctx.audio, ctx.lang.code)
            ctx.transcript = result.value["text"]
            ctx.transcript_chunks = result.value["chunks"]
        except (ServiceError, BudgetExceeded) as exc:
            ctx.warnings.append(f"transcription failed: {exc}")
            skip(ctx, "back_transcription", "roundtrip", str(exc)[:200])

    def _length_ratios(self, ctxs: list[ItemContext]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = defaultdict(list)
        for c in ctxs:
            if c.translation and not c.failed:
                ratio = length_features(c.source, c.translation)
                if ratio is not None:
                    out[c.lang.code].append(ratio)
        return out

    def _speech_rates(self, ctxs: list[ItemContext]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = defaultdict(list)
        for c in ctxs:
            if c.audio_stats and c.speakable and c.audio_stats.speech_duration > 0.5 and len(c.speakable.text) >= 20:
                out[c.lang.code].append(len(c.speakable.text) / c.audio_stats.speech_duration)
        return out

    def _should_judge(self, ctx: ItemContext) -> str | None:
        if not ctx.translation:
            return "no translation"
        if self.judge is None or not self.judge.available:
            return "LLM judge not configured" if self.judge is None else f"LLM unavailable: {self.judge.llm.disabled}"
        if self.opts.judge_policy == "off":
            return "--judge off"
        if self._whole_text_failure(ctx):
            return "skipped: cheap checks already found a whole-text failure"
        if self.opts.judge_policy == "suspicious":
            text_signals = [s for s in ctx.signals if s.category.area == "translation"]
            metrics = self._evidence(ctx)
            if not text_signals and metrics.get("back_translation_source_coverage", 1.0) >= 0.85 and metrics.get("reference_chrf++", 100) >= 50:
                return "--judge suspicious: no signal"
        return None

    def _evidence(self, ctx: ItemContext) -> dict:
        out: dict = {}
        for check in ctx.checks:
            if check.name == "back_translation" and check.status == "ok":
                out["back_translation_chrf++"] = check.metrics.get("chrf++_vs_source")
                out["back_translation_source_coverage"] = check.metrics.get("source_coverage")
            elif check.name == "reference_chrf" and check.status == "ok":
                out["reference_chrf++"] = check.metrics.get("chrf++")
            elif check.name == "length_ratio" and check.status == "ok" and check.metrics.get("z") is not None:
                out["length_ratio_z"] = check.metrics.get("z")
        return out

    async def _judge(self, ctx: ItemContext, variant: int = 0) -> JudgeOutcome | None:
        reason = self._should_judge(ctx)
        if reason:
            if variant == 0:
                skip(ctx, "llm_judge", "judge", reason)
            return None
        text_signals = [s for s in ctx.signals if s.category.area == "translation"]
        candidates = group_candidates(text_signals)
        try:
            outcome = await self.judge.judge(
                language=ctx.lang.name,
                source=ctx.source,
                translation=ctx.translation or "",
                back_translation=ctx.back_translation,
                reference=ctx.item.reference,
                candidates=candidates,
                evidence=self._evidence(ctx),
                variant=variant,
            )
        except Exception as exc:  # noqa: BLE001 - the judge is optional; deterministic flags still stand
            if variant == 0:
                ctx.warnings.append(f"judge failed: {type(exc).__name__}: {str(exc)[:200]}")
                skip(ctx, "llm_judge", "judge", f"failed: {type(exc).__name__}")
            return None
        if variant == 0:
            ctx.checks.append(
                CheckResult(
                    name="llm_judge",
                    stage="judge",
                    cache="hit" if outcome.cached else "miss",
                    metrics={
                        "candidates": len(candidates),
                        "confirmed": sum(1 for v in outcome.verdicts.values() if v[0] == "confirm"),
                        "rejected": sum(1 for v in outcome.verdicts.values() if v[0] == "reject"),
                        "errors": len(outcome.errors),
                        "redacted_chars": outcome.redactions,
                    },
                )
            )
            ctx.candidates = candidates
        return outcome

    async def _consistency(self, ctxs: list[ItemContext], outcomes: dict[str, JudgeOutcome]) -> dict | None:
        if self.opts.judge_repeats <= 0 or not outcomes:
            return None
        judged = sorted((c for c in ctxs if c.uid in outcomes), key=lambda c: hashlib.sha256(c.uid.encode()).hexdigest())
        sample = judged[: self.opts.judge_repeat_sample]
        log.info("phase", stage="judge_consistency", count=len(sample) * self.opts.judge_repeats)
        pairs: list[tuple[str, str, bool, bool]] = []

        async def rerun(ctx: ItemContext):
            base = outcomes[ctx.uid].signature()
            for r in range(1, self.opts.judge_repeats + 1):
                again = await self._judge(ctx, variant=r)
                if again is None:
                    continue
                sig = again.signature()
                pairs.append((ctx.uid, ctx.lang.code, sig == base, sig[0] == base[0]))

        await self._each(sample, rerun)
        if not pairs:
            return None
        by_lang: dict[str, list[bool]] = defaultdict(list)
        for _, lang, same, _ in pairs:
            by_lang[lang].append(same)
        return {
            "items": len({p[0] for p in pairs}),
            "repeats": self.opts.judge_repeats,
            "comparisons": len(pairs),
            "verdict_agreement": round(sum(p[2] for p in pairs) / len(pairs), 4),
            "binary_agreement": round(sum(p[3] for p in pairs) / len(pairs), 4),
            "by_lang": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_lang.items())},
            "definition": "verdict = (has errors, set of error categories); agreement = share of re-judgements equal to the first",
        }

    def _result(self, ctx: ItemContext, outcome: JudgeOutcome | None) -> ItemResult:
        labels = [
            {
                "category": (lb.category.value if lb.category else None),
                "type": lb.type,
                "severity": lb.sev.value,
                "span": lb.span,
                "field": lb.field,
                "area": lb.area,
                "annotator": lb.annotator,
            }
            for lb in ctx.item.labels
        ]
        uid = ctx.uid
        if ctx.failed:
            return ItemResult(
                id=uid,
                lang=ctx.item.lang,
                status="failed",
                error=ctx.failed,
                translation=ctx.translation,
                checks=ctx.checks,
                labels=labels,
                warnings=ctx.warnings,
            )
        if ctx.translation is None and ctx.audio is None:
            return ItemResult(
                id=uid, lang=ctx.item.lang, status="skipped", error="no translation or audio to score", checks=ctx.checks, labels=labels, warnings=ctx.warnings
            )
        candidates = ctx.candidates
        flags, rejected = fuse(translation=ctx.translation or "", source=ctx.source, signals=ctx.signals, candidates=candidates, outcome=outcome, th=self.th)
        penalty, score = mqm(flags, ctx.source)
        content = "markdown" if "\n" in ctx.source.strip() or "|" in ctx.source else "text"
        if "$" in ctx.source or "\\(" in ctx.source or "\\[" in ctx.source:
            content = "latex" if content == "text" else content + "+latex"
        return ItemResult(
            id=uid,
            lang=ctx.item.lang,
            status="scored",
            content_type=content,
            translation=ctx.translation,
            back_translation=ctx.back_translation,
            transcript=ctx.transcript,
            audio=ctx.audio_path,
            audio_duration_s=ctx.audio_stats.duration if ctx.audio_stats else None,
            checks=ctx.checks,
            flags=flags,
            rejected=rejected,
            mqm_penalty=penalty,
            quality_score=score,
            labels=labels,
            warnings=ctx.warnings,
        )


def _safe(uid: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in uid)
