"""Tracing and report generator for the task agent."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class Tracer:
    """Thread-safe recorder for step-by-step trace, result.md, and report.json."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.artifacts_dir = out_dir / "artifacts"
        self.trace_file = out_dir / "trace.jsonl"
        self.report_file = out_dir / "report.json"
        self.result_file = out_dir / "result.md"
        self._lock = threading.Lock()

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        # Truncate stale trace.jsonl when --out is reused across runs
        self.trace_file.write_text("", encoding="utf-8")

        self.steps: List[Dict[str, Any]] = []
        self.artifacts: List[Dict[str, str]] = []
        self.assumptions: List[str] = []
        self.verification_results: List[Dict[str, Any]] = []

    def record_step(
        self,
        step_number: int,
        plan: str,
        tool: str,
        arguments: Dict[str, Any],
        result_summary: str,
        latency_ms: int,
        cost_inr: float,
    ) -> None:
        """Record a single execution step to trace.jsonl."""
        entry = {
            "step": step_number,
            "plan": plan,
            "tool": tool,
            "arguments": arguments,
            "result_summary": result_summary,
            "latency_ms": latency_ms,
            "cost_inr": round(cost_inr, 4),
        }
        with self._lock:
            self.steps.append(entry)
            with open(self.trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def add_artifact(self, name: str, relative_path: str, description: str) -> None:
        """Register a produced artifact file."""
        self.artifacts.append({
            "name": name,
            "path": relative_path,
            "description": description,
        })

    def add_assumption(self, assumption: str) -> None:
        """Record an explicit assumption made during the run."""
        if assumption not in self.assumptions:
            self.assumptions.append(assumption)

    def add_verification(self, check_name: str, passed: bool, details: str) -> None:
        """Record a verification check."""
        self.verification_results.append({
            "check": check_name,
            "passed": passed,
            "details": details,
        })

    def write_report(
        self,
        status: str,
        total_time_seconds: float,
        total_cost_inr: float,
        task_description: str,
        final_answer: str,
        deduplicated_calls: int = 0,
    ) -> None:
        """Generate out/report.json and out/result.md."""
        # 1. Write report.json
        report_data = {
            "status": status,
            "task": task_description,
            "total_steps": len(self.steps),
            "total_time_seconds": total_time_seconds,
            "total_cost_inr": round(total_cost_inr, 4),
            "deduplicated_calls": deduplicated_calls,
            "verification_results": self.verification_results,
            "artifacts_count": len(self.artifacts),
            "artifacts": self.artifacts,
        }
        self.report_file.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")

        # 2. Write result.md
        dedup_info = f" | **De-duplicated Calls:** {deduplicated_calls}" if deduplicated_calls > 0 else ""
        md_lines = [
            f"# Task Execution Result: {status.upper()}",
            "",
            f"**Task:** {task_description}  ",
            f"**Total Steps:** {len(self.steps)} | **Execution Time:** {total_time_seconds:.2f}s | **Total Cost:** ₹{total_cost_inr:.4f}{dedup_info}  ",
            "",
            "## Summary & Final Answer",
            final_answer,
            "",
        ]

        if self.assumptions:
            md_lines.extend([
                "## Assumptions Made",
                *[f"- {a}" for a in self.assumptions],
                "",
            ])

        if self.artifacts:
            md_lines.extend([
                "## Generated Artifacts",
                "| Name | File | Description |",
                "|---|---|---|",
                *[f"| {a['name']} | [{Path(a['path']).name}](./{a['path']}) | {a['description']} |" for a in self.artifacts],
                "",
            ])

        if self.verification_results:
            md_lines.extend([
                "## Verification Checks",
                "| Check | Status | Details |",
                "|---|---|---|",
                *[f"| {v['check']} | {'PASSED' if v['passed'] else 'FAILED'} | {v['details']} |" for v in self.verification_results],
                "",
            ])

        md_lines.extend([
            "## Step-by-Step Execution Trace",
            f"Trace logged to `trace.jsonl` ({len(self.steps)} steps).",
        ])

        self.result_file.write_text("\n".join(md_lines), encoding="utf-8")
