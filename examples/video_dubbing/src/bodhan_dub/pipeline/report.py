"""Machine-readable artifacts: transcript.en.json, segments.<lang>.json, report.json."""

from __future__ import annotations

import json
import os

from ..types import DubSegment, LanguageResult, Segment
from . import qa as qa_mod


def _dump(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_transcript(segments: list[Segment], duration: float, path: str) -> None:
    _dump(path, {
        "language": "en",
        "duration_s": round(duration, 3),
        "segments": [s.to_dict() for s in segments],
    })


def write_segments(dubs: list[DubSegment], path: str) -> None:
    _dump(path, [d.to_dict() for d in dubs])


def build_report(settings, ingest_info: dict, results: list[LanguageResult],
                 cost: dict, run_timings: dict, path: str) -> dict:
    any_failed = any(r.status == "failed" for r in results)
    report = {
        "input": settings.input,
        "duration_s": round(ingest_info.get("duration", 0.0), 3),
        "languages_requested": settings.langs,
        "code_mixed": settings.code_mixed,
        "mock": settings.mock,
        "status": "failed" if any_failed else "ok",
        "config": {
            "asr_model": settings.asr_model,
            "vad": settings.vad,
            "concurrency": settings.concurrency,
            "stretch_band": [settings.min_stretch, settings.max_stretch],
            "target_lufs": settings.target_lufs,
            "max_condense_rounds": settings.max_condense_rounds,
        },
        "timings_s": run_timings,
        "cost_inr": cost,
        "languages": {},
        "quality_targets": {
            "segment_sync": "start within ±300 ms of source",
            "speed_change": "0.85×–1.2×",
            "total_length": "within ±100 ms of input",
            "loudness": "−16 LUFS ±1",
        },
    }
    for r in results:
        report["languages"][r.lang] = {
            "status": r.status,
            "error": r.error,
            "video": r.video_path,
            "subtitles": r.subtitles_path,
            "burned_video": r.burned_video_path,
            "timings_s": r.timings,
            "cost_inr": round(r.cost_inr, 4),
            "qa": qa_mod.aggregate(r.segments),
            "loudness": r.timings.get("loudness"),
        }
    _dump(path, report)
    return report
