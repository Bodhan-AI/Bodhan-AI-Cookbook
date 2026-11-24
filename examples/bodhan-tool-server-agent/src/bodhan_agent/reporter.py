"""Reporting and Artifact Generation for Bodhan Task Agent.

Generates the complete run dossier:
- `result.md`: Human-readable markdown executive summary
- `report.json`: Structured, schema-validated JSON run report
- `trace.jsonl`: Comprehensive JSON Lines step-by-step audit trail
- `artifacts/`: Directory holding all intermediate and generated media
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from bodhan_agent.models import ExecutionStatus, RunReport, StepTrace


class AgentReporter:
    """Manages output reporting, audit logging, and artifact persistence."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir.resolve()
        self.artifacts_dir = self.output_dir / "artifacts"
        self.trace_file = self.output_dir / "trace.jsonl"
        self.report_file = self.output_dir / "report.json"
        self.result_file = self.output_dir / "result.md"

        # Ensure directory structure exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def log_trace(self, trace: StepTrace) -> None:
        """Append a step execution trace to trace.jsonl."""
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(trace.model_dump_json() + "\n")

    def write_report_json(self, report: RunReport) -> Path:
        """Write the structured machine-readable report.json."""
        with open(self.report_file, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
        return self.report_file

    def write_result_md(self, report: RunReport, traces: List[StepTrace]) -> Path:
        """Generate human-readable result.md summary."""
        status_badges = {
            ExecutionStatus.SUCCESS: "✅ **SUCCESS**",
            ExecutionStatus.PARTIAL: "⚠️ **PARTIAL SUCCESS**",
            ExecutionStatus.IMPOSSIBLE: "🛑 **IMPOSSIBLE TASK**",
            ExecutionStatus.BUDGET_EXCEEDED: "💸 **BUDGET EXCEEDED**",
            ExecutionStatus.FAILED: "❌ **FAILED**",
        }
        badge = status_badges.get(report.status, str(report.status))

        lines = [
            "# Bodhan Task Execution Report",
            "",
            f"**Status:** {badge}",
            f"**Task:** {report.task}",
            "",
            "## Summary Metrics",
            "",
            f"| Metric | Value | Budget / Limit |",
            f"| :--- | :--- | :--- |",
            f"| **Steps Taken** | {report.steps_taken} | {report.max_steps} steps |",
            f"| **Total Cost** | ₹{report.total_cost_inr:.4f} | ₹{report.max_cost_inr:.2f} max |",
            f"| **Runtime** | {report.duration_seconds:.2f}s | - |",
            "",
        ]

        # Cost Breakdown Table
        if report.cost_breakdown:
            lines.extend([
                "### Incurred Cost Breakdown (INR)",
                "",
                "| Service / Tool | Incurred Cost (₹) |",
                "| :--- | :--- |",
            ])
            for tool, cost in sorted(report.cost_breakdown.items()):
                lines.append(f"| Indic-{tool.capitalize()} | ₹{cost:.4f} |")
            lines.append("")

        # Reason or Explanation (Crucial for impossible or budget exceeded tasks)
        if report.reason:
            lines.extend([
                "## Execution Notice & Details",
                "",
                f"> ℹ️ **Notice:** {report.reason}",
                "",
            ])

        # Verification Checks Table
        if report.verification_results:
            lines.extend([
                "## Output Verification Results",
                "",
                "| Check Name | Status | Details |",
                "| :--- | :--- | :--- |",
            ])
            for v in report.verification_results:
                v_badge = "✅ PASS" if v.passed else "❌ FAIL"
                lines.append(f"| `{v.check_name}` | {v_badge} | {v.details} |")
            lines.append("")

        # Artifacts Manifest
        if report.artifacts:
            lines.extend([
                "## Generated Artifacts",
                "",
            ])
            for a in report.artifacts:
                p = Path(a)
                lines.append(f"- [{p.name}](artifacts/{p.name}) (`{p.suffix}` file)")
            lines.append("")

        # Step-by-Step Execution Log
        if traces:
            lines.extend([
                "## Execution Trace Overview",
                "",
                "| Step | Tool Called | Status | Duration | Cost (₹) |",
                "| :--- | :--- | :--- | :--- | :--- |",
            ])
            for t in traces:
                lines.append(
                    f"| {t.step_number} | `{t.tool}` | {t.status} | {t.duration_seconds:.2f}s | ₹{t.cost_inr:.4f} |"
                )
            lines.append("")

        with open(self.result_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return self.result_file
