"""The machine-readable run report.

Two rules:

* every number here must be traceable to a log line or an artifact;
* anything not measured says ``not_measured`` rather than being omitted or
  guessed at.

The quality targets from the brief are pre-declared and filled in as stages
land, so a reader can see at a glance what this run did and did not verify.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "bodhan-dub/report/v1"

QUALITY_TARGETS: dict[str, dict[str, str]] = {
    "segment_sync": {
        "target": "each dubbed segment starts within 300 ms of its source segment",
        "status": "not_measured",
    },
    "speed_change": {
        "target": "any time-stretch stays within 0.85x and 1.20x",
        "status": "not_measured",
    },
    "coverage": {
        "target": "every source speech segment dubbed, or skipped with a logged reason",
        "status": "not_measured",
    },
    "content_fidelity": {
        "target": "dubbed audio back-transcribed; per-segment character error rate reported",
        "status": "not_measured",
    },
    "total_length": {
        "target": "output video matches input duration within 100 ms",
        "status": "not_measured",
    },
    "loudness": {
        "target": "speech normalised to -16 LUFS, plus or minus 1",
        "status": "not_measured",
    },
    "runtime": {
        "target": "a 5 minute video in 3 languages finishes in under 30 minutes",
        "status": "not_measured",
    },
}


class RunReport:
    def __init__(self, *, input_path: Path, out_dir: Path, langs: list[str], stage: str) -> None:
        self.path = out_dir / "report.json"
        self.data: dict[str, Any] = {
            "schema": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "input": {"path": str(input_path), "exists": input_path.exists()},
            "requested": {"langs": langs, "stage": stage},
            "stages": {},
            "languages": {code: {"status": "pending"} for code in langs},
            "transcript": None,
            "settings": {},
            "cost": {},
            "cache": {},
            "ratelimit": {},
            "rate_limits": {},
            "quality_targets": {key: dict(value) for key, value in QUALITY_TARGETS.items()},
            "warnings": [],
            "errors": [],
        }

    # -- stages -----------------------------------------------------------

    def stage(self, name: str, status: str, **fields: Any) -> None:
        entry = self.data["stages"].setdefault(name, {})
        entry["status"] = status
        entry.update(fields)

    def set_transcript(self, payload: dict[str, Any]) -> None:
        stats = payload.get("stats", {})
        self.data["transcript"] = {
            "path": f"transcript.{payload.get('asr', {}).get('language', 'en')}.json",
            "model": payload.get("asr", {}).get("model"),
            "timing": payload.get("asr", {}).get("timing"),
            "segments": stats.get("segments"),
            "speech_coverage": stats.get("speech_coverage"),
            "failures": stats.get("failures"),
        }
        self.data["input"].update(
            {
                "duration_s": payload.get("input", {}).get("duration_s"),
                "has_video": payload.get("input", {}).get("has_video"),
                "has_audio": payload.get("input", {}).get("has_audio"),
                "video_codec": payload.get("input", {}).get("video_codec"),
                "audio_codec": payload.get("input", {}).get("audio_codec"),
            }
        )
        coverage_status = "pass" if not stats.get("failures") else "partial"
        self.data["quality_targets"]["coverage"]["status"] = coverage_status
        self.data["quality_targets"]["coverage"]["observed"] = (
            f"{stats.get('segments')} segments, {stats.get('failures')} failed chunks"
        )

    def set_language(self, code: str, status: str, **fields: Any) -> None:
        entry = self.data["languages"].setdefault(code, {})
        entry["status"] = status
        entry.update(fields)

    # -- diagnostics ------------------------------------------------------

    def warn(self, message: str) -> None:
        self.data["warnings"].append(message)

    def error(self, message: str) -> None:
        self.data["errors"].append(message)

    def set_settings(self, public: dict[str, Any]) -> None:
        self.data["settings"] = public

    def set_cost(self, summary: dict[str, Any]) -> None:
        self.data["cost"] = summary

    def set_cache(self, stats: dict[str, Any]) -> None:
        self.data["cache"] = stats

    def set_rate_limits(self, limits: dict[str, str]) -> None:
        self.data["rate_limits"] = limits

    def set_ratelimit(self, stats: dict[str, Any]) -> None:
        self.data["ratelimit"] = stats

    # -- output -----------------------------------------------------------

    def write(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return self.path
