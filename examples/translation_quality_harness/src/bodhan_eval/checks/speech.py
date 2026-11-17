"""Speech checks: signal sanity, then indic-transcribe back-transcription aligned to the spoken text.

ASR makes its own mistakes, mostly near-miss spellings scattered through the clip. Those
align as similar-word substitutions and are counted as checker noise. What we flag are the
shapes ASR noise does not produce: contiguous deletions (omission, truncation), inserted
runs that copy their neighbourhood (repetition), and dense dissimilar stretches that line up
with signal anomalies (garbled audio).
"""

from __future__ import annotations

from statistics import median

import regex

from ..audio.analysis import analyze
from ..config import Thresholds
from ..models import Category, Severity, Signal
from ..text.align import Op, align_words, error_rate, runs, similarity
from ..text.normalize import normalize
from ..text.segment import words
from .base import ItemContext

PRIOR_CHARS_PER_SECOND = 14.0


class SpeechRateModel:
    """Characters of speakable text per second of speech, per language, fitted on the run's own clips."""

    def __init__(self, rates_by_lang: dict[str, list[float]]):
        self.rates = {lang: median(r) for lang, r in rates_by_lang.items() if len(r) >= 5}
        pooled = [x for r in rates_by_lang.values() for x in r]
        self.pooled = median(pooled) if len(pooled) >= 5 else PRIOR_CHARS_PER_SECOND

    def cps(self, lang: str) -> float:
        return self.rates.get(lang, self.pooled)


def _time_to_text(ctx: ItemContext, start_s: float, end_s: float) -> tuple[int, int] | None:
    stats, sp = ctx.audio_stats, ctx.speakable
    if not stats or not sp or not sp.text or stats.speech_duration <= 0:
        return None
    n = len(sp.text)
    a = int((start_s - stats.speech_start) / stats.speech_duration * n)
    b = int((end_s - stats.speech_start) / stats.speech_duration * n) + 1
    a, b = max(0, min(a, n - 1)), max(1, min(b, n))
    return sp.span_to_translation(a, b)


def check_audio_signal(ctx: ItemContext, th: Thresholds, rate_model: SpeechRateModel | None) -> None:
    with ctx.check("audio_signal", "deterministic") as res:
        if ctx.audio is None:
            res.status = "skipped"
            res.reason = "no audio"
            return
        stats = ctx.audio_stats or analyze(ctx.audio, noise_flatness=th.noise_flatness, min_noise_s=th.min_noise_s)
        ctx.audio_stats = stats
        res.metrics = stats.to_dict()
        full = (0, len(ctx.translation or ""))
        if stats.silent or stats.duration < 0.2:
            ctx.add(
                Signal(
                    check="audio_signal",
                    category=Category.SPEECH_OMISSION,
                    score=0.95,
                    severity=Severity.CRITICAL,
                    span=full,
                    audio_span=(0.0, stats.duration),
                    message=f"audio is silent or empty ({stats.duration:.2f}s)",
                    hard=True,
                )
            )
            return
        for s, e in stats.internal_silences:
            length = e - s
            if length >= th.min_internal_silence_s:
                ctx.add(
                    Signal(
                        check="audio_signal",
                        category=Category.SPEECH_GARBLED,
                        score=round(min(0.55 + 0.1 * (length - th.min_internal_silence_s), 0.85), 3),
                        severity=Severity.MAJOR,
                        audio_span=(s, e),
                        span=_time_to_text(ctx, s, e),
                        message=f"{length:.1f}s of silence inside the speech at {s:.1f}s",
                        data={"silence_s": round(length, 3)},
                    )
                )
        if stats.clipped_fraction >= th.clipped_fraction and stats.clipped_segments:
            s, e = stats.clipped_segments[0][0], stats.clipped_segments[-1][1]
            ctx.add(
                Signal(
                    check="audio_signal",
                    category=Category.SPEECH_GARBLED,
                    score=0.8,
                    severity=Severity.MAJOR,
                    audio_span=(s, e),
                    span=_time_to_text(ctx, s, e),
                    message=f"{stats.clipped_fraction:.2%} of samples are clipped ({len(stats.clipped_segments)} regions)",
                    hard=True,
                    data={"clipped_fraction": stats.clipped_fraction},
                )
            )
        for s, e in stats.noise_segments:
            ctx.add(
                Signal(
                    check="audio_signal",
                    category=Category.SPEECH_GARBLED,
                    score=0.7,
                    severity=Severity.MAJOR,
                    audio_span=(s, e),
                    span=_time_to_text(ctx, s, e),
                    message=f"{e - s:.1f}s noise-like stretch (flat spectrum) at {s:.1f}s",
                    data={"noise_s": round(e - s, 3)},
                )
            )
        if rate_model and ctx.speakable and len(ctx.speakable.text) >= 20:
            expected = len(ctx.speakable.text) / rate_model.cps(ctx.lang.code)
            ratio = stats.speech_duration / expected if expected else 1.0
            res.metrics["duration_ratio"] = round(ratio, 3)
            if ratio < th.duration_ratio_low:
                n = len(ctx.speakable.text)
                cut = max(int(n * ratio), 0)
                ctx.add(
                    Signal(
                        check="audio_signal",
                        category=Category.SPEECH_OMISSION,
                        score=0.4,
                        severity=Severity.MAJOR,
                        span=ctx.speakable.span_to_translation(cut, n),
                        audio_span=(stats.speech_end, stats.duration),
                        message=f"speech lasts {stats.speech_duration:.1f}s, {ratio:.0%} of the ~{expected:.1f}s this text needs",
                        data={"duration_ratio": ratio},
                    )
                )
            elif ratio > th.duration_ratio_high:
                ctx.add(
                    Signal(
                        check="audio_signal",
                        category=Category.SPEECH_REPETITION,
                        score=0.3,
                        severity=Severity.MAJOR,
                        message=f"speech lasts {stats.speech_duration:.1f}s, {ratio:.1f}x the ~{expected:.1f}s this text needs",
                        data={"duration_ratio": ratio},
                    )
                )


def _asr_norm(text: str):
    return normalize(text, fold_case=True, strip_nukta=True, drop_punct=True)


def _wild(token: str) -> bool:
    return bool(regex.fullmatch(r"[0-9]+|[a-z]+", token))


def check_transcription(ctx: ItemContext, th: Thresholds) -> None:
    with ctx.check("back_transcription", "roundtrip") as res:
        if ctx.transcript is None or ctx.speakable is None:
            res.status = "skipped"
            res.reason = "no transcript"
            return
        ref_norm = _asr_norm(ctx.speakable.text)
        hyp_norm = _asr_norm(ctx.transcript)
        ref = words(ref_norm.text)
        hyp = words(hyp_norm.text)
        if not ref:
            res.status = "skipped"
            res.reason = "nothing speakable in the translation"
            return
        ops = align_words([w.text for w in ref], [w.text for w in hyp], wildcard=_wild)
        subs = [o for o in ops if o.kind == "sub"]
        near_miss = sum(1 for o in subs if o.sim >= 0.5)
        errors = sum(1 for o in ops if o.kind in {"sub", "del", "ins"})
        wer = errors / len(ref)
        cer = error_rate(ref_norm.text.replace(" ", ""), hyp_norm.text.replace(" ", ""))
        res.metrics = {
            "wer": round(wer, 4),
            "cer": round(cer, 4),
            "ref_words": len(ref),
            "hyp_words": len(hyp),
            "near_miss_substitutions": near_miss,
            "deletions": sum(1 for o in ops if o.kind == "del"),
            "insertions": sum(1 for o in ops if o.kind == "ins"),
        }

        def to_translation(i0: int, i1: int) -> tuple[int, int]:
            s, e = ref_norm.span_to_orig(ref[i0].start, ref[i1].end)
            return ctx.speakable.span_to_translation(s, e)

        def time_of(i0: int, i1: int) -> tuple[float, float] | None:
            stats = ctx.audio_stats
            if not stats or stats.speech_duration <= 0:
                return None
            total = len(ref)
            return (round(stats.speech_start + stats.speech_duration * i0 / total, 2), round(stats.speech_start + stats.speech_duration * (i1 + 1) / total, 2))

        deletions = [r for r in runs(ops, {"del"}) if len(r) >= 1]
        flagged_del = 0
        for run in deletions:
            i0, i1 = run[0].i, run[-1].i
            length = i1 - i0 + 1
            tail = all(o.kind in {"del", "ins"} for o in ops[ops.index(run[-1]) :])
            if length < th.asr_omission_min_words and not (tail and length >= 2):
                continue
            flagged_del += length
            share = length / len(ref)
            other_errors = (errors - length) / max(len(ref) - length, 1)
            reliability = 0.6 if other_errors > 0.5 else 1.0
            score = (0.8 if tail else 0.7) * reliability
            kind = "the last" if tail else "a run of"
            ctx.add(
                Signal(
                    check="back_transcription",
                    category=Category.SPEECH_OMISSION,
                    score=round(score, 3),
                    severity=Severity.CRITICAL if share > 0.3 else Severity.MAJOR,
                    span=to_translation(i0, i1),
                    audio_span=time_of(i0, i1),
                    message=f"{kind} {length} words of the text ({share:.0%}) are missing from the transcript" + (" (truncated)" if tail else ""),
                    data={"words": length, "tail": tail},
                )
            )

        _repetitions(ctx, ops, ref, hyp, to_translation, time_of)
        _garbled(ctx, th, ops, to_translation, time_of)
        res.metrics["omitted_words"] = flagged_del


def _repetitions(ctx: ItemContext, ops: list[Op], ref, hyp, to_translation, time_of) -> None:
    hyp_words = [w.text for w in hyp]
    reported: set[int] = set()
    for run in runs(ops, {"ins"}):
        if len(run) < 2:
            continue
        j0, j1 = run[0].j, run[-1].j
        size = j1 - j0 + 1
        inserted = " ".join(hyp_words[j0 : j1 + 1])
        before = " ".join(hyp_words[max(j0 - size, 0) : j0])
        after = " ".join(hyp_words[j1 + 1 : j1 + 1 + size])
        # The aligner may attach either copy to the reference, so the duplicate can sit on either side.
        pos = ops.index(run[0])
        if before and similarity(inserted, before) >= 0.7:
            ref_idx = [o.i for o in ops[:pos] if o.i is not None]
            if not ref_idx:
                continue
            i1 = ref_idx[-1]
            i0 = max(i1 - size + 1, 0)
        elif after and similarity(inserted, after) >= 0.7:
            ref_idx = [o.i for o in ops[pos + len(run) :] if o.i is not None]
            if not ref_idx:
                continue
            i0 = ref_idx[0]
            i1 = min(i0 + size - 1, len(ref) - 1)
        else:
            continue
        if i0 in reported:
            continue
        reported.add(i0)
        ctx.add(
            Signal(
                check="back_transcription",
                category=Category.SPEECH_REPETITION,
                score=0.8,
                severity=Severity.MAJOR,
                span=to_translation(i0, i1),
                audio_span=time_of(i0, i1),
                message=f"{size} words are spoken twice in a row",
                data={"words": size},
            )
        )


def _garbled(ctx: ItemContext, th: Thresholds, ops: list[Op], to_translation, time_of) -> None:
    window = th.asr_garbled_window
    ref_ops = [o for o in ops if o.i is not None]
    k = 0
    while k + window <= len(ref_ops):
        chunk = ref_ops[k : k + window]
        bad = [o for o in chunk if o.kind == "sub" and o.sim < 0.4]
        if len(bad) == window:
            end = k + window
            while end < len(ref_ops) and ref_ops[end].kind == "sub" and ref_ops[end].sim < 0.4:
                end += 1
            i0, i1 = ref_ops[k].i, ref_ops[end - 1].i
            ctx.add(
                Signal(
                    check="back_transcription",
                    category=Category.SPEECH_GARBLED,
                    score=0.45,
                    severity=Severity.MAJOR,
                    span=to_translation(i0, i1),
                    audio_span=time_of(i0, i1),
                    message=f"{end - k} consecutive words are unintelligible to the recogniser",
                    data={"words": end - k},
                )
            )
            k = end
        else:
            k += 1
