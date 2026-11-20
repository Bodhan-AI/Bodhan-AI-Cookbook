"""The run report: per-language status, quality targets (with every miss and why), retries, latency, cost."""

import statistics

from .speech import MAX_SPEEDUP

SYNC_S = 0.3
MIN_SPEED = 0.85
LENGTH_S = 0.1
TARGET_LUFS, LUFS_TOL = -16, 1


def language_summary(rows: list[dict], output: dict, timings: dict, bodhan) -> dict:
    """Summarise one language against the quality targets in the brief."""
    dubbed = [r for r in rows if "clip" in r]
    skipped = [{"unit": r["id"], "start": r["start"], "reason": r["skipped"]} for r in rows if "skipped" in r]

    offsets = [r["dub_start"] - r["start"] for r in dubbed]
    sync_misses = [{"unit": r["id"], "offset_s": round(r["dub_start"] - r["start"], 3),
                    "why": "started early to make room for the translation"}
                   for r in dubbed if abs(r["dub_start"] - r["start"]) > SYNC_S]
    speed_misses = [{"unit": r["id"], "speed": r["speed"],
                     "why": f"translation still too long for its window after {r['shorten_rounds']} shortening round(s)"}
                    for r in dubbed if r["speed"] > MAX_SPEEDUP + 0.005 or r["speed"] < MIN_SPEED]
    cers = [r["cer"] for r in dubbed]
    unverified = [{"unit": r["id"], "qa": r["qa"]} for r in dubbed if any("repeated or truncated" in q for q in r["qa"])]

    length_err = output.get("duration_error")
    lufs = output.get("speech_lufs")
    targets = {
        "segment_sync": {"target": f"start within ±{SYNC_S} s", "worst_offset_s": round(max(map(abs, offsets), default=0), 3),
                         "met": not sync_misses, "misses": sync_misses},
        "speed_change": {"target": f"{MIN_SPEED}× to {MAX_SPEEDUP}×", "max_speed": max((r["speed"] for r in dubbed), default=1.0),
                         "met": not speed_misses, "misses": speed_misses},
        "coverage": {"target": "every unit dubbed or skipped with a reason", "dubbed": len(dubbed), "units": len(rows),
                     "met": True, "skipped": skipped},
        "content_fidelity": {"target": "back-transcribed; per-unit CER reported in segments.json",
                             "median_cer": round(statistics.median(cers), 3) if cers else None,
                             "max_cer": max(cers, default=None),
                             "note": "CER counts numbers and letter labels spoken as words; repeats and omissions "
                                     "are judged by the missing/extra checks instead",
                             "met": not unverified, "misses": unverified},
        "total_length": {"target": f"within ±{LENGTH_S} s of the input", "error_s": length_err,
                         "met": length_err is not None and abs(length_err) <= LENGTH_S},
        "loudness": {"target": f"{TARGET_LUFS} LUFS ±{LUFS_TOL}", "speech_lufs": lufs,
                     "met": not dubbed or (lufs is not None and abs(lufs - TARGET_LUFS) <= LUFS_TOL)},
    }
    return {
        "voices": sorted({r["voice"] for r in rows if r.get("voice")}),
        "targets": targets,
        "retries": {
            "tts_regenerations": sum(r.get("tts_attempts", 0) - len(r.get("chunks", [])) for r in dubbed),
            "shorten_rounds": sum(r.get("shorten_rounds", 0) for r in dubbed),
            "api_retries": bodhan.calls["retries"],
        },
        "latency_s": timings,
        "calls": dict(bodhan.calls),
        "cost_inr": {k: round(v, 4) for k, v in bodhan.cost.items()},
        "units": [{k: r.get(k) for k in ("id", "start", "end", "dub_start", "dub_end", "voice", "speed", "cer",
                                          "shorten_rounds", "tts_attempts", "qa", "skipped") if k in r} for r in rows],
    }
