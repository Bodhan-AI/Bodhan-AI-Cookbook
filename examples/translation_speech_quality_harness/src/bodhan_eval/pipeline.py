"""Orchestration: dataset -> checks -> fusion -> verification -> calibration -> report.

Order and cost policy
1. Free deterministic checks run on every item (script, numbers, format, length, entities, reference).
2. Back-translation (Indic-Translate, ~₹0.0003 per item) runs on every item: it feeds the number,
   entity and coverage checks and the judge.
3. The LLM judge runs on every translation, except items already proven to be in the wrong
   language (the judge cannot add anything there).
4. Speech: supplied audio is always checked (ASR is cheap). Missing audio is synthesized only with
   --generate, and only for items selected by a budget-aware triage (speech is the costly step).
5. The verifier (a focused LLM second look) runs only on uncertain clusters: those whose score is
   within `verify_band` of the threshold. Confident flags and clear non-flags are not re-checked.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio as audiolib
from .budget import PRICE_SPEAK_PER_CHAR, BudgetExceeded, Ledger
from .cache import Cache
from .calibrate import cross_validate, evaluate, per_check, records_from, tune
from .checks import REGISTRY, Context, Signal
from .checks.length import baselines
from .checks.speech import FRAME_S, frame_db, spoken_tokens
from .clients import BodhanClient, CreditExhausted, LLMClient, LLMUnavailable, MissingKey, ModelError
from .config import Settings
from .dataset import Item, Label, load_dataset, load_labels, resolve_audio
from .fuse import Cluster, cluster_signals, flags_for, load_thresholds
from .judge import JUDGE_VERSION, Judge
from .langs import get_lang
from .text.markup import speakable
from .text.normalize import sentences

log = logging.getLogger("bodhan_eval")


@dataclass
class Options:
    dataset: Path
    out: Path
    langs: list[str]
    generate: bool = False
    baseline: Path | None = None
    calibration: Path | None = None
    max_cost_inr: float | None = None
    thresholds: Path | None = None
    speech_sample: int | None = None  # max items to synthesize (None = as budget allows)
    consistency_sample: int = 30
    max_verify: int = 300
    no_llm: bool = False
    item_concurrency: int = 8
    fail_on_regression: int | None = None
    review_queue: int = 40
    freeze_thresholds: bool = False


class Run:
    def __init__(self, opts: Options, settings: Settings | None = None, bodhan: BodhanClient | None = None, llm: LLMClient | None = None):
        self.o = opts
        self.s = settings or Settings.from_env()
        self.cache = Cache(self.s.cache_dir)
        self.ledger = Ledger(opts.max_cost_inr)
        self.bodhan = bodhan or BodhanClient(self.s, self.cache, self.ledger)
        if llm is not None:
            self.llm = llm
        elif self.s.llm_enabled and not opts.no_llm:
            self.llm = LLMClient(self.s, self.cache, self.ledger)
        else:
            self.llm = None
        self.judge = Judge(self.llm) if self.llm else None
        self.cfg = load_thresholds(opts.thresholds)
        self.paid_stopped: str | None = None  # set when credit/budget runs out; later paid calls are skipped
        self.llm_stopped: str | None = None  # set when the LLM provider's daily quota is exhausted
        self.audio_dir = opts.out / "audio"

    # ----------------------------------------------------------------- helpers
    def _stop_llm(self) -> None:
        """A daily quota will not recover within this run: stop calling the provider, but keep using cached answers."""
        self.llm_stopped = "LLM provider quota exhausted for today (cached judge results are still used)"
        if self.llm is not None and hasattr(self.llm, "cache_only"):
            self.llm.cache_only = True
        log.warning("LLM calls stopped", extra={"event": "llm.stop"})

    async def _paid(self, coro, ctx: Context | None, what: str):
        """Run a paid model call; on budget/credit exhaustion, stop further paid calls gracefully."""
        if self.paid_stopped:
            if ctx is not None:
                ctx.notes.append(f"{what} skipped: {self.paid_stopped}")
            coro.close()
            return None
        try:
            return await coro
        except (BudgetExceeded, CreditExhausted) as e:
            self.paid_stopped = str(e)
            log.warning("paid calls stopped", extra={"event": "budget.stop", "reason": str(e)})
            if ctx is not None:
                ctx.notes.append(f"{what} skipped: {e}")
            return None
        except (ModelError, MissingKey, ValueError, OSError) as e:
            if ctx is not None:
                ctx.notes.append(f"{what} failed: {str(e)[:200]}")
            log.warning("model call failed", extra={"event": f"{what}.error", "item": ctx.item.id if ctx else None, "error": str(e)[:300]})
            return None

    async def _run_check(self, name: str, ctx: Context) -> list[Signal]:
        chk = REGISTRY[name]
        if not chk.applies(ctx):
            return []
        try:
            sigs = await chk.run(ctx)
        except Exception as e:  # noqa: BLE001 - one broken check must not stop the item
            ctx.notes.append(f"check {name} crashed: {type(e).__name__}: {str(e)[:150]}")
            log.exception("check crashed", extra={"event": "check.crash", "item": ctx.item.id, "check": name})
            return []
        ctx.checks_run.append(name)
        for s in sigs:
            log.info("signal", extra={"event": "signal", "item": ctx.item.id, "check": name, "category": s.category,
                                      "confidence": round(s.confidence, 3), "span": s.span, "detail": s.message})
        return sigs

    # ----------------------------------------------------------------- stages
    async def translation_stage(self, item: Item, ctx: Context, sem: asyncio.Semaphore) -> list[Signal]:
        async with sem:
            sigs: list[Signal] = []
            bt = await self._paid(self.bodhan.translate(ctx.translation, "en"), ctx, "backtranslation")
            if bt is not None:
                ctx.artifacts["backtranslation"] = bt["text"]
            for name in ("script_lang", "numbers", "format", "length", "entities", "reference", "backtranslation"):
                sigs += await self._run_check(name, ctx)
            wrong_lang = any(s.category == "wrong_language" and s.confidence >= 0.9 and s.span == (0, len(ctx.translation)) for s in sigs)
            if self.judge and not wrong_lang:
                try:
                    jsigs, meta = await self.judge.judge(ctx)
                    ctx.artifacts["judge"] = meta
                    ctx.checks_run.append("judge")
                    sigs += jsigs
                except LLMUnavailable:
                    ctx.notes.append(f"judge skipped: {self.llm_stopped}")
                except Exception as e:  # noqa: BLE001 - provider errors must not stop the run
                    msg = str(e)
                    if _is_quota_error(msg):
                        self._stop_llm()
                    ctx.notes.append(f"judge failed: {type(e).__name__}: {msg[:150]}")
                    log.warning("judge failed", extra={"event": "judge.error", "item": item.id, "error": msg[:300]})
            elif wrong_lang:
                ctx.notes.append("judge skipped: translation is in the wrong language")
            return sigs

    async def synthesize(self, item: Item, ctx: Context) -> tuple[bytes, list[dict]] | None:
        plain, pidx = speakable(ctx.translation)
        chunks: list[tuple[int, int]] = []
        for s, e in sentences(plain):
            if chunks and (e - chunks[-1][0]) <= 220:
                chunks[-1] = (chunks[-1][0], e)
            else:
                chunks.append((s, e))
        voice = (self.o_voice(ctx))
        pcm, segs, sr = [], [], 24000
        for s, e in chunks:
            text = plain[s:e].strip()
            if not any(ch.isalpha() for ch in text):
                continue
            wav = await self._paid(self.bodhan.speak(text, item.lang, voice), ctx, "speech synthesis")
            if wav is None:
                return None
            x, sr = audiolib.decode(wav)
            pcm.append(x)
            segs.append({"text_span": [pidx[s], pidx[e - 1] + 1]})
        if not pcm:
            return None
        x, times = audiolib.concat(pcm, sr)
        for seg, (t0, t1) in zip(segs, times, strict=True):
            seg["start_s"], seg["end_s"] = t0, t1
        return audiolib.encode(x, sr), segs

    def o_voice(self, ctx: Context) -> str:
        return ctx.lang.voices[0] if ctx.lang.voices else "Kavya"

    async def speech_stage(self, item: Item, ctx: Context, sem: asyncio.Semaphore) -> list[Signal]:
        async with sem:
            x, sr = ctx.artifacts["audio"]
            sigs = await self._run_check("audio_signal", ctx)
            texts = []
            for a, b in audiolib.split_for_asr(x, sr):
                piece = audiolib.encode(x[a:b], sr)
                t = await self._paid(self.bodhan.transcribe(piece, item.lang), ctx, "transcription")
                if t is None:
                    return sigs
                texts.append(t)
            ctx.artifacts["asr_text"] = " ".join(texts)
            sigs += await self._run_check("asr_alignment", ctx)
            return sigs

    # ----------------------------------------------------------------- main
    async def execute(self) -> dict:
        o = self.o
        o.out.mkdir(parents=True, exist_ok=True)
        items, problems = load_dataset(o.dataset, o.langs or None)
        dataset_dir = o.dataset.resolve().parent
        if o.calibration:
            ext = load_labels(o.calibration)
            for it in items:
                if it.id in ext:
                    it.labels = ext[it.id]
        log.info("dataset loaded", extra={"event": "dataset.loaded", "items": len(items), "problems": len(problems)})
        unknown = [lang for lang in o.langs if get_lang(lang).script == "Unknown"]
        if unknown:
            log.warning("unknown language codes; script checks will be skipped for them", extra={"event": "langs.unknown", "langs": unknown})

        ctxs: dict[str, Context] = {}
        results: dict[str, dict] = {}
        flags_meta: dict[str, dict] = {}
        # ---- 1. translations (generate if asked)
        gen_sem = asyncio.Semaphore(o.item_concurrency)

        async def ensure_translation(it: Item):
            ctx = Context(it, get_lang(it.lang), it.translation or "", {})
            ctxs[it.id] = ctx
            if not it.translation and o.generate:
                async with gen_sem:
                    out = await self._paid(self.bodhan.translate(it.source, it.lang), ctx, "translation")
                if out:
                    ctx.translation = out["text"]
                    ctx.artifacts["translation_generated"] = True
                    if out.get("truncated"):
                        ctx.notes.append("Indic-Translate output was truncated (finish_reason=length)")

        await asyncio.gather(*(ensure_translation(it) for it in items))
        stats = {"length": baselines([(it.lang, it.source, ctxs[it.id].translation) for it in items if ctxs[it.id].translation])}
        for c in ctxs.values():
            c.stats = stats

        # ---- 2. translation checks
        sem = asyncio.Semaphore(o.item_concurrency)
        todo = [it for it in items if ctxs[it.id].translation]
        signals: dict[str, list[Signal]] = {it.id: [] for it in items}
        done = 0

        async def tstage(it):
            nonlocal done
            try:
                signals[it.id] += await self.translation_stage(it, ctxs[it.id], sem)
            except Exception as e:  # noqa: BLE001
                ctxs[it.id].notes.append(f"error: {type(e).__name__}: {str(e)[:200]}")
                results[it.id] = {"status": "error"}
                log.exception("item failed", extra={"event": "item.error", "item": it.id})
            done += 1
            if done % 25 == 0 or done == len(todo):
                log.info(f"translation checks {done}/{len(todo)}", extra={"event": "progress", "stage": "translation", "done": done})

        await asyncio.gather(*(tstage(it) for it in todo))

        # ---- 3. speech: load supplied audio; synthesize for triaged items if --generate
        for it in items:
            p = resolve_audio(it, dataset_dir)
            if p is None:
                continue
            if not p.exists():
                ctxs[it.id].notes.append(f"audio file not found: {it.audio}")
                continue
            try:
                ctxs[it.id].artifacts["audio"] = audiolib.decode(p.read_bytes())
            except Exception as e:  # noqa: BLE001
                ctxs[it.id].notes.append(f"audio unreadable: {str(e)[:150]}")
        if o.generate:
            await self._synthesize_triaged(items, ctxs, signals)
        stats["speech_rate"] = self._speech_rates(items, ctxs)
        speech_items = [it for it in items if ctxs[it.id].artifacts.get("audio") is not None and ctxs[it.id].translation]
        done = 0

        async def sstage(it):
            nonlocal done
            try:
                signals[it.id] += await self.speech_stage(it, ctxs[it.id], sem)
            except Exception as e:  # noqa: BLE001
                ctxs[it.id].notes.append(f"speech error: {type(e).__name__}: {str(e)[:200]}")
                log.exception("speech failed", extra={"event": "item.error", "item": it.id})
            done += 1
            if done % 25 == 0 or done == len(speech_items):
                log.info(f"speech checks {done}/{len(speech_items)}", extra={"event": "progress", "stage": "speech", "done": done})

        await asyncio.gather(*(sstage(it) for it in speech_items))

        # ---- 4. fuse, then verify uncertain clusters (the targeted, cost-aware LLM step)
        clusters: dict[str, list[Cluster]] = {it.id: cluster_signals(signals[it.id]) for it in items}
        if self.judge:
            await self._verify_uncertain(items, ctxs, clusters)

        # ---- 5. judge consistency on a fixed sample
        consistency = await self._consistency(items, ctxs) if self.judge and o.consistency_sample else None

        # ---- 6. calibration
        for it in items:
            ctx = ctxs[it.id]
            status = results.get(it.id, {}).get("status")
            if not ctx.translation:
                status = "skipped"
                ctx.notes.append("no translation supplied (use --generate to create one)")
            results[it.id] = {"id": it.id, "lang": it.lang, "status": status or "ok", "labels": it.labels,
                              "group": (it.model_extra or {}).get("group") or it.id}
        records = records_from(list(results.values()), clusters)
        calibration = {"definitions": _DEFINITIONS, "judge": {"model": self.llm.model if self.llm else None, "rubric": JUDGE_VERSION}}
        cfg = self.cfg
        if records:
            calibration["before_tuning"] = evaluate(records, cfg)
            if o.freeze_thresholds:
                notes = {"all": f"thresholds frozen (from {o.thresholds}); measured, not re-tuned"}
            else:
                cfg, notes = tune(records, cfg)
            calibration["tuning_notes"] = notes
            calibration["after_tuning_in_sample"] = evaluate(records, cfg)
            calibration["cross_validated"] = cross_validate(records, self.cfg)
            calibration["per_check"] = per_check(records)
        calibration["thresholds_used"] = cfg["thresholds"]
        calibration["judge_consistency"] = consistency
        calibration["labels_source"] = str(o.calibration) if o.calibration else ("inline dataset labels" if records else None)

        # ---- 7. final flags per item
        final = []
        for it in items:
            ctx = ctxs[it.id]
            r = results[it.id]
            fl = flags_for(clusters[it.id], cfg) if r["status"] not in ("skipped", "error") else []
            r.update({
                "status": r["status"] if r["status"] in ("skipped", "error") else ("flagged" if fl else "clean"),
                "flags": fl,
                "checks_run": ctx.checks_run,
                "notes": ctx.notes,
                "metrics": {k: v for k, v in {
                    "chrf_pp": ctx.artifacts.get("chrf_pp"),
                    "asr_error_rate": ctx.artifacts.get("asr_error_rate"),
                    **({"audio": ctx.artifacts["audio_stats"]} if ctx.artifacts.get("audio_stats") else {}),
                }.items() if v is not None},
                "back_translation": ctx.artifacts.get("backtranslation"),
                "asr_text": ctx.artifacts.get("asr_text"),
                "translation": ctx.translation if ctx.artifacts.get("translation_generated") else None,
                "generated": {"translation": bool(ctx.artifacts.get("translation_generated")), "audio": bool(ctx.artifacts.get("audio_generated"))},
                "source_chars": len(it.source),
                "source_words": len(it.source.split()),
                "uncertain": [c.category for c in clusters[it.id] if abs(c.score(cfg["weights"], cfg.get("deny_weight", 0.8)) - cfg["thresholds"][c.category]) < 0.1],
            })
            flags_meta[it.id] = r
            final.append(r)
        await self.close()
        return {"items": final, "problems": problems, "calibration": calibration, "cfg": cfg,
                "cost": self.ledger.summary(), "run": {**self.ledger.run_summary(), "cache": self.cache.stats(), "paid_calls_stopped": self.paid_stopped},
                "llm_model": self.llm.model if self.llm else None}

    async def close(self) -> None:
        await self.bodhan.aclose()
        if self.llm:
            await self.llm.aclose()

    # ----------------------------------------------------------------- speech triage
    async def _synthesize_triaged(self, items, ctxs, signals) -> None:
        """Pick items for speech synthesis, cheapest-to-check first, within budget.

        Skipped: items with audio already, nothing speakable, or a translation already flagged as
        the wrong language (speech checks against wrong text are meaningless). Order is a stable hash,
        stratified by language, so samples are reproducible and balanced.
        """
        cands = []
        for it in items:
            ctx = ctxs[it.id]
            if ctx.artifacts.get("audio") is not None or not ctx.translation:
                continue
            if any(s.category == "wrong_language" and s.confidence >= 0.9 for s in signals[it.id]):
                ctx.notes.append("speech not generated: translation is in the wrong language")
                continue
            plain, _ = speakable(ctx.translation)
            chars = len(plain.strip())
            if chars < 5:
                continue
            h = hashlib.sha256(it.id.encode()).hexdigest()
            cands.append((it, chars, h))
        by_lang: dict[str, list] = {}
        for c in sorted(cands, key=lambda c: c[2]):
            by_lang.setdefault(c[0].lang, []).append(c)
        order = []
        while any(by_lang.values()):
            for lang in sorted(by_lang):
                if by_lang[lang]:
                    order.append(by_lang[lang].pop(0))
        if self.o.speech_sample is not None:
            order = order[: self.o.speech_sample]
        budget_left = None
        if self.o.max_cost_inr is not None:
            budget_left = self.o.max_cost_inr - self.ledger.spent_total
        selected = []
        for it, chars, _ in order:
            cost = chars * PRICE_SPEAK_PER_CHAR * 1.15  # + ASR and margin
            if budget_left is not None:
                if cost > budget_left:
                    ctxs[it.id].notes.append("speech not generated: --max-cost-inr budget")
                    continue
                budget_left -= cost
            selected.append(it)
        log.info("speech triage", extra={"event": "speech.triage", "candidates": len(cands), "selected": len(selected)})
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(self.o.item_concurrency)

        async def one(it):
            async with sem:
                res = await self.synthesize(it, ctxs[it.id])
            if res:
                wav, segs = res
                (self.audio_dir / f"{_safe(it.id)}.wav").write_bytes(wav)
                ctxs[it.id].artifacts["audio"] = audiolib.decode(wav)
                ctxs[it.id].artifacts["audio_generated"] = True
                ctxs[it.id].artifacts["segments"] = segs

        await asyncio.gather(*(one(it) for it in selected))

    def _speech_rates(self, items, ctxs) -> dict:
        by: dict[str, list[float]] = {}
        for it in items:
            ctx = ctxs[it.id]
            a = ctx.artifacts.get("audio")
            if a is None or not ctx.translation:
                continue
            x, sr = a
            db = frame_db(x, sr)
            if not len(db):
                continue
            voiced = np.where(db >= max(-55.0, np.percentile(db, 95) - 35.0))[0]
            chars = sum(len(t[0]) for t in spoken_tokens(ctx.translation))
            if len(voiced) < 10 or chars < 15:
                continue
            spoken = (voiced[-1] - voiced[0] + 1) * FRAME_S
            r = math.log(chars / spoken)
            by.setdefault(it.lang, []).append(r)
            by.setdefault("_all", []).append(r)
        out = {}
        for k, v in by.items():
            if len(v) >= 3:
                med = statistics.median(v)
                mad = statistics.median(abs(x - med) for x in v)
                out[k] = (med, max(1.4826 * mad, 0.06), len(v))
        return out

    # ----------------------------------------------------------------- agentic follow-ups
    async def _verify_uncertain(self, items, ctxs, clusters) -> None:
        # The band is anchored to the packaged defaults, not to run-specific (tuned/frozen) thresholds, so the
        # same clusters are verified in every run of the same items and the cached verdicts make runs comparable.
        cfg = load_thresholds()
        vt = cfg.get("verify_band_thresholds", cfg["thresholds"])  # fixed band centres, independent of calibration
        band = cfg.get("verify_band", 0.25)
        w, dw = cfg["weights"], cfg.get("deny_weight", 0.8)
        jobs = []
        for it in items:
            for c in clusters[it.id]:
                if c.category.startswith("speech") or not ctxs[it.id].translation:
                    continue
                if any(s.check in ("numbers", "format", "script_lang") and s.confidence >= 0.9 for s in c.signals):
                    continue  # deterministic and confident: nothing for an LLM to add
                sc = c.score(w, dw)
                if abs(sc - vt[c.category]) <= band:
                    jobs.append((it, c, sc))
        jobs.sort(key=lambda j: abs(j[2] - vt[j[1].category]))
        jobs = jobs[: self.o.max_verify]
        log.info("verifying uncertain clusters", extra={"event": "verify.start", "clusters": len(jobs)})
        sem = asyncio.Semaphore(self.o.item_concurrency)

        async def one(it, c):
            async with sem:
                lead = max(c.signals, key=lambda s: w.get(s.check, 0.5) * s.confidence)
                try:
                    ok, conf, reason = await self.judge.verify(ctxs[it.id], lead)
                except LLMUnavailable:
                    ctxs[it.id].notes.append(f"verifier skipped: {self.llm_stopped}")
                    return
                except Exception as e:  # noqa: BLE001
                    if _is_quota_error(str(e)):
                        self._stop_llm()
                    ctxs[it.id].notes.append(f"verifier failed: {str(e)[:120]}")
                    return
            if ok is None:
                return
            c.verified = ok
            c.verifier_reason = reason
            if ok:
                c.signals.append(Signal("verifier", c.category, lead.severity, conf, reason or "Verifier confirmed the error.", lead.span, lead.source_span))
            else:
                c.denial = conf
            ctxs[it.id].checks_run.append("verifier")

        await asyncio.gather(*(one(it, c) for it, c, _ in jobs))

    async def _consistency(self, items, ctxs) -> dict | None:
        cands = [it for it in items if ctxs[it.id].artifacts.get("judge", {}).get("status") == "ok"]
        cands.sort(key=lambda it: hashlib.sha256(("consistency" + it.id).encode()).hexdigest())
        sample = cands[: self.o.consistency_sample]
        if not sample:
            return None
        sem = asyncio.Semaphore(self.o.item_concurrency)

        async def one(it):
            async with sem:
                try:
                    a, _ = await self.judge.judge(ctxs[it.id], sample=0)
                    b, _ = await self.judge.judge(ctxs[it.id], sample=1)
                except Exception as e:  # noqa: BLE001 - an unavailable re-judge just shrinks the sample
                    if _is_quota_error(str(e)):
                        self._stop_llm()
                    return None
            return it.lang, bool(a), bool(b), {s.category for s in a}, {s.category for s in b}

        rows = [r for r in await asyncio.gather(*(one(it) for it in sample)) if r is not None]
        if not rows:
            return {"skipped": self.llm_stopped or "no re-judge succeeded"}
        verdict = sum(x[1] == x[2] for x in rows) / len(rows)
        cats = sum(x[3] == x[4] for x in rows) / len(rows)
        per_lang: dict[str, list[int]] = {}
        for lang, a, b, _, _ in rows:
            per_lang.setdefault(lang, []).append(int(a == b))
        return {"items": len(rows), "same_verdict": round(verdict, 4), "same_categories": round(cats, 4),
                "per_language_same_verdict": {k: round(sum(v) / len(v), 4) for k, v in sorted(per_lang.items())},
                "note": "The same prompt is sent twice as independent requests (temperature 0); the second request is cached separately."}


def _is_quota_error(msg: str) -> bool:
    """A daily/billing quota will not recover within a run (unlike a per-minute rate limit)."""
    m = msg.lower()
    return any(k in m for k in ("per day", "tokens per day", "requests per day", "insufficient_quota", "quota exceeded", "billing"))


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


_DEFINITIONS = {
    "detection_recall": "share of labelled errors whose item has at least one flag in the same area (translation/speech)",
    "false_alarm_rate": "share of verified-clean items (labels == []) with any flag",
    "localization": "share of detected errors (with a labelled span) where a flag's span overlaps the labelled span",
    "category_accuracy": "share of detected errors where a flag has the labelled category",
    "cross_validated": "grouped 5-fold CV: thresholds tuned on 4 folds, metrics measured on the held-out fold",
}

__all__ = ["Options", "Run", "Label"]
