"""
Report generator for Bodhan Task Agent.
Produces report.json and result.md matching the assignment requirements.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Reporter:
    """Collects metadata, steps, verification, and generates final report documents."""

    def __init__(self, out_dir: Path, task: str) -> None:
        self.out_dir = Path(out_dir)
        self.task = task
        self.status = "running"  # "completed" | "partial" | "failed" | "refused"
        self.steps: list[dict[str, Any]] = []
        self.artifacts: list[str] = []
        self.assumptions: list[str] = []
        self.verification_results: list[dict[str, Any]] = []
        self.budget_snapshot: dict[str, Any] = {}

    def add_step(self, step: dict[str, Any]) -> None:
        self.steps.append(step)

    def add_artifact(self, path: str) -> None:
        if path not in self.artifacts:
            self.artifacts.append(path)

    def add_assumption(self, assumption: str) -> None:
        if assumption not in self.assumptions:
            self.assumptions.append(assumption)

    def add_verification(self, v: dict[str, Any]) -> None:
        self.verification_results.append(v)

    def set_status(self, status: str) -> None:
        self.status = status

    def set_budget(self, snapshot: dict[str, Any]) -> None:
        self.budget_snapshot = snapshot

    def write(self, summary: str = "") -> None:
        """Write report.json and result.md into out_dir."""
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # 1. report.json
        report_data = {
            "status": self.status,
            "task": self.task,
            "total_steps": len(self.steps),
            "steps": self.steps,
            "artifacts": self.artifacts,
            "assumptions": self.assumptions,
            "verification_results": self.verification_results,
            "budget": self.budget_snapshot,
        }
        report_path = self.out_dir / "report.json"
        report_path.write_text(
            json.dumps(report_data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # 2. result.md
        lines: list[str] = [
            "# Task Execution Result\n\n",
            f"**Task:** {self.task}\n\n",
            f"**Status:** `{self.status.upper()}`\n\n",
        ]

        if self.budget_snapshot:
            cost = self.budget_snapshot.get("cost_inr", 0.0)
            elapsed = self.budget_snapshot.get("elapsed_secs", 0.0)
            steps = self.budget_snapshot.get("steps_used", len(self.steps))
            lines.append(
                f"**Metrics:** {steps} step(s) | ₹{cost:.2f} INR | {elapsed:.1f}s\n\n"
            )

        if self.assumptions:
            lines.append("## Assumptions & Language Detection\n\n")
            for a in self.assumptions:
                lines.append(f"- {a}\n")
            lines.append("\n")

        if summary:
            lines.append("## Summary of Work\n\n")
            lines.append(f"{summary}\n\n")

        if self.artifacts:
            lines.append("## Output Artifacts\n\n")
            for art in self.artifacts:
                art_p = Path(art)
                lines.append(f"- [{art_p.name}](./{art_p.name})\n")
            lines.append("\n")

        if self.verification_results:
            lines.append("## Verification Checks\n\n")
            for v in self.verification_results:
                passed = "✅ PASSED" if v.get("passed") else "⚠️ WARNING / FAILED"
                lines.append(f"- **{passed}:** {json.dumps(v, default=str)}\n")
            lines.append("\n")

        result_path = self.out_dir / "result.md"
        result_path.write_text("".join(lines), encoding="utf-8")
