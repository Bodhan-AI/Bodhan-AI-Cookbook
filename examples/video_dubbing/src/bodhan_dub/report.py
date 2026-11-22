"""Score each language against the brief's quality targets, and say why any target is missed."""

from __future__ import annotations

from collections.abc import Sequence
from statistics import mean, median
from typing import Any

from .synth import MAX_TEMPO, DubLine
from .translate import LanguageResult
from .ttsqa import CER_LIMIT

TARGETS = {
    "segment_sync_ms": 300,  # each dubbed segment starts within ±300 ms of its source segment
    "stretch_range": [0.85, MAX_TEMPO],  # any time-stretch stays within this range
    "coverage": "every source speech segment is dubbed, or skipped with a logged reason",
    "cer": (f"back-transcription CER per segment against the translation; 'cer' is number-aware (flag above "
            f"{CER_LIMIT}), 'cer_plain' is ordinary CER, which counts spoken numbers and English words as errors"),
    "total_length_ms": 100,  # output duration within ±100 ms of the input
    "speech_lufs": [-17.0, -15.0],  # -16 LUFS ±1
    "runtime": "a 5-minute video in 3 languages under 30 minutes on hosted APIs",
}


def language_report(lang: str, translated: LanguageResult, lines: Sequence[DubLine], *, input_s: float,
                    output_s: float | None, speech_lufs: float | None, timings: dict[str, float]) -> dict[str, Any]:
    spoken = [ln for ln in lines if ln.sentences]
    skipped = [{"id": ln.unit.id, "reason": "; ".join(ln.notes) or ln.unit.note} for ln in lines if not ln.sentences]
    sync = [round((ln.placed_start - ln.unit.start) * 1000) for ln in spoken]
    tempos = [ln.tempo for ln in spoken]
    cers = [c for ln in spoken if (c := ln.cer) is not None]
    plain = [c for ln in spoken if (c := ln.cer_plain) is not None]
    length_ms = round((output_s - input_s) * 1000) if output_s is not None else None
    misses: list[str] = []

    late = [(ln.unit.id, s) for ln, s in zip(spoken, sync, strict=True) if abs(s) > TARGETS["segment_sync_ms"]]
    if late:
        misses.append(f"sync: {len(late)} segment(s) start more than 300 ms late because an earlier line ran "
                      f"past its slot even after condensing and a {MAX_TEMPO}x stretch: "
                      + ", ".join(f"{i} (+{s} ms)" for i, s in late[:5]))
    off_range = [ln.unit.id for ln in spoken if not 0.85 - 1e-6 <= ln.tempo <= MAX_TEMPO + 1e-6]
    if off_range:
        misses.append(f"stretch outside 0.85-{MAX_TEMPO}x: {off_range}")
    if skipped:
        misses.append(f"coverage: {len(skipped)} unit(s) not dubbed (reasons in 'skipped')")
    high_cer = [(ln.unit.id, ln.cer) for ln in spoken if ln.cer is not None and ln.cer > CER_LIMIT]
    if high_cer:
        misses.append(f"content fidelity: {len(high_cer)} segment(s) with CER > {CER_LIMIT}: "
                      + ", ".join(f"{i} ({c:.2f})" for i, c in high_cer[:5])
                      + ". These were the best of several takes; see each sentence's takes in segments.<lang>.json")
    if length_ms is not None and abs(length_ms) > TARGETS["total_length_ms"]:
        misses.append(f"total length off by {length_ms} ms (container/codec frame rounding)")
    if speech_lufs is not None and not -17.0 <= speech_lufs <= -15.0:
        misses.append(f"speech loudness {speech_lufs} LUFS outside -16 ±1")

    return {
        "status": translated.status,
        "error": translated.error,
        "timings_s": {k: round(v, 1) for k, v in timings.items()},
        "coverage": {"units": len(lines), "dubbed": len(spoken),
                     "flagged": sum(ln.status == "flagged" for ln in lines), "skipped": skipped},
        "sync_ms": {"within_300ms": sum(abs(s) <= 300 for s in sync), "of": len(sync),
                    "max": max(sync, default=0), "mean": round(mean(sync), 1) if sync else 0},
        "stretch": {"max_tempo": round(max(tempos, default=1.0), 3), "stretched_units": sum(t > 1.0001 for t in tempos),
                    "outside_range": len(off_range)},
        "cer": {"mean": round(mean(cers), 3) if cers else None, "median": round(median(cers), 3) if cers else None,
                "max": round(max(cers), 3) if cers else None, "over_limit": len(high_cer),
                "plain_mean": round(mean(plain), 3) if plain else None,
                "plain_max": round(max(plain), 3) if plain else None},
        "total_length_ms": length_ms,
        "speech_lufs": speech_lufs,
        "retries": {
            "translation_alternatives": sum(max(0, len(u.candidates) - 1) for u in translated.units),
            "tts_retakes": sum(len(s.takes) - 1 for ln in spoken for s in ln.sentences),
            "condense_rounds": sum(len(ln.condensed) for ln in lines),
            "judge_calls": translated.judge_calls, "judge_failed": translated.judge_failed,
        },
        "misses": misses,
    }


# The brief's runtime target: a 5-minute video in 3 languages in under 30 minutes on hosted APIs,
# i.e. 2 minutes of wall clock per minute of video per language.
RUNTIME_S_PER_VIDEO_MIN_PER_LANG = 120.0


def runtime_check(runtime_s: float, video_s: float, langs: int, cooldown_s: dict[str, float]) -> dict[str, Any]:
    budget = RUNTIME_S_PER_VIDEO_MIN_PER_LANG * (video_s / 60) * max(1, langs)
    waited = round(sum(cooldown_s.values()), 1)
    check: dict[str, Any] = {"runtime_s": round(runtime_s, 1), "budget_s": round(budget, 1),
                             "within_target": runtime_s <= budget, "rate_limit_wait_s": cooldown_s}
    if runtime_s > budget:
        check["miss"] = (f"runtime {runtime_s:.0f}s over the {budget:.0f}s target; about {waited:.0f}s of "
                         "pauses were rate-limit cooldowns imposed by the API keys (threads overlap, so this "
                         "is an upper bound)")
    return check
