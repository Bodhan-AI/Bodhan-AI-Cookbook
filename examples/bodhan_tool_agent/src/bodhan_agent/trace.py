"""Trace logger and artifact report generator (trace.jsonl, report.json, result.md)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from bodhan_agent.models import ExecutionPlan, TaskSpec, TraceEvent, VerificationResult

logger = logging.getLogger("bodhan_agent.trace")


class TraceLogger:
    """Manages event stream emission and produces final human-readable and machine-readable reports."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.out_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.trace_file = self.out_dir / "trace.jsonl"
        self.report_file = self.out_dir / "report.json"
        self.result_file = self.out_dir / "result.md"

        self.events: list[TraceEvent] = []

    def record_event(self, event: TraceEvent):
        """Append an event to memory and immediately stream it to trace.jsonl."""
        self.events.append(event)
        # Sanitize sensitive fields before writing
        raw_dict = event.model_dump()
        if raw_dict.get("sanitized_arguments"):
            for k in list(raw_dict["sanitized_arguments"].keys()):
                if "key" in k.lower() or "token" in k.lower() or "secret" in k.lower():
                    raw_dict["sanitized_arguments"][k] = "[REDACTED]"

        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(raw_dict, ensure_ascii=False) + "\n")

    def generate_final_report(
        self,
        task_spec: TaskSpec,
        plan: ExecutionPlan,
        verifications: list[VerificationResult],
        total_actual_cost_inr: float,
        total_runtime_s: float,
        status: str,
        error_message: str | None = None,
        generated_artifacts: list[str] | None = None,
        assumptions: list[str] | None = None,
    ):
        """Generate structured report.json and human-readable result.md."""
        completed_steps = [s for s in plan.steps if s.status.value == "completed"]
        failed_steps = [s for s in plan.steps if s.status.value == "failed"]
        artifacts = generated_artifacts or []

        # 1. report.json
        report_data = {
            "task_id": task_spec.task_id,
            "status": status,
            "task_description": task_spec.user_prompt,
            "total_steps": len(plan.steps),
            "completed_steps": len(completed_steps),
            "failed_steps": len(failed_steps),
            "verification_results": [v.model_dump() for v in verifications],
            "total_estimated_cost_inr": plan.estimated_total_cost_inr,
            "total_actual_cost_inr": round(total_actual_cost_inr, 4),
            "total_runtime_seconds": round(total_runtime_s, 2),
            "retries": sum(s.retry_count for s in plan.steps),
            "assumptions": assumptions or [],
            "errors": [error_message] if error_message else [],
            "generated_artifacts": artifacts,
        }

        self.report_file.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")

        # 2. result.md
        md_lines = [
            f"# Execution Result: {task_spec.task_id}",
            "",
            f"**Task:** {task_spec.user_prompt}  ",
            f"**Status:** {status.upper()}  ",
            f"**Runtime:** {total_runtime_s:.2f} seconds  ",
            f"**Total Cost (INR):** ₹{total_actual_cost_inr:.4f} (Estimated: ₹{plan.estimated_total_cost_inr:.4f})  ",
            f"**Steps Completed:** {len(completed_steps)}/{len(plan.steps)}",
            "",
            "## Completed Actions",
            "",
        ]

        if completed_steps:
            for s in completed_steps:
                dur = f"({s.duration_ms / 1000.0:.1f}s)" if s.duration_ms else ""
                md_lines.append(f"- **{s.tool_name}**: {s.description} {dur}")
        else:
            md_lines.append("- None")

        if failed_steps or error_message:
            md_lines.extend(["", "## Failures and Limitations", ""])
            if error_message:
                md_lines.append(f"- **Error:** {error_message}")
            for fs in failed_steps:
                md_lines.append(f"- **Failed Step ({fs.tool_name}):** {fs.error_message}")

        if assumptions:
            md_lines.extend(["", "## Assumptions", ""])
            for a in assumptions:
                md_lines.append(f"- {a}")

        md_lines.extend(["", "## Verification Results", ""])
        if verifications:
            for v in verifications:
                badge = "✅ PASS" if v.status.value == "passed" else ("⚠️ PARTIAL" if v.status.value == "partial" else "❌ FAIL")
                md_lines.append(f"- **{v.check_name}**: {badge} — {v.details}")
        else:
            md_lines.append("- No explicit verification rules evaluated.")

        md_lines.extend(["", "## Generated Artifacts", ""])
        if artifacts:
            for art in artifacts:
                md_lines.append(f"- [`{Path(art).name}`]({Path(art).as_posix()})")
        else:
            md_lines.append("- None")

        self.result_file.write_text("\n".join(md_lines), encoding="utf-8")
        logger.info(f"Artifacts successfully written to {self.out_dir}")
