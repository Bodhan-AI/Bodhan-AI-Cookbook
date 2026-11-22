"""
Writes trace.jsonl (one line per step) and report.json (final summary).

trace.jsonl schema per line:
  step, tool, args_summary, result_summary, latency_s, cost_inr, cached, verified, error

report.json schema:
  status, task, timestamp, steps_used, steps_limit, cost_used_inr, cost_limit_inr, verification
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class StepRecord:
    step: int
    tool: str
    args_summary: str        # short human-readable summary of key args
    result_summary: str      # short human-readable summary of the result
    latency_s: float
    cost_inr: float
    cached: bool = False
    verified: bool | None = None
    error: str | None = None


class TraceWriter:
    def __init__(self, out_dir: Path) -> None:
        self._trace_path = out_dir / "trace.jsonl"
        self._report_path = out_dir / "report.json"

    def record(self, record: StepRecord) -> None:
        with self._trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record)) + "\n")

    def write_report(
        self,
        *,
        status: str,
        task: str,
        budget_summary: dict,
        verification_results: list[dict],
    ) -> None:
        report = {
            "status": status,
            "task": task,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **budget_summary,
            "verification": verification_results,
        }
        self._report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
