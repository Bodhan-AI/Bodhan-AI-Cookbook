"""
Execution tracing, metric collection, and structured artifact generation.
"""

import os
import json
import time
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class StepTrace(BaseModel):
    step_number: int
    plan: str
    tool: str
    arguments: Dict[str, Any]
    result_summary: str
    latency_ms: float
    cost_inr: float
    timestamp: float = Field(default_factory=time.time)


class VerificationCheck(BaseModel):
    artifact_name: str
    check_type: str  # e.g. "back_transcription", "coverage", "format_check"
    passed: bool
    details: str


class RunReport(BaseModel):
    task: str
    status: str  # "COMPLETED", "IMPOSSIBLE_TASK", "BUDGET_EXCEEDED", "FAILED"
    total_steps: int
    total_cost_inr: float
    total_duration_seconds: float
    cache_hits: int
    cache_misses: int
    verifications: List[VerificationCheck] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)
    explanation: Optional[str] = None
    final_answer: Optional[str] = None


class AgentTracer:
    """Manages output directory, step logging to trace.jsonl, and report generation."""

    def __init__(self, out_dir: str, task: str):
        self.out_dir = os.path.abspath(out_dir)
        self.task = task
        self.artifacts_dir = os.path.join(self.out_dir, "artifacts")
        os.makedirs(self.artifacts_dir, exist_ok=True)

        self.trace_file = os.path.join(self.out_dir, "trace.jsonl")
        self.report_file = os.path.join(self.out_dir, "report.json")
        self.result_file = os.path.join(self.out_dir, "result.md")

        self.steps: List[StepTrace] = []
        self.verifications: List[VerificationCheck] = []
        self.artifacts: List[str] = []
        self.start_time = time.time()
        self.total_cost_inr: float = 0.0

        # Clear previous run trace file if it exists
        if os.path.exists(self.trace_file):
            os.remove(self.trace_file)

    def record_step(
        self,
        step_number: int,
        plan: str,
        tool: str,
        arguments: Dict[str, Any],
        result_summary: str,
        latency_ms: float,
        cost_inr: float,
    ) -> StepTrace:
        step = StepTrace(
            step_number=step_number,
            plan=plan,
            tool=tool,
            arguments=arguments,
            result_summary=result_summary,
            latency_ms=round(latency_ms, 2),
            cost_inr=round(cost_inr, 4),
        )
        self.steps.append(step)
        self.total_cost_inr += cost_inr

        # Append to trace.jsonl
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(step.model_dump_json() + "\n")

        return step

    def add_verification(self, artifact_name: str, check_type: str, passed: bool, details: str):
        self.verifications.append(
            VerificationCheck(
                artifact_name=artifact_name,
                check_type=check_type,
                passed=passed,
                details=details,
            )
        )

    def register_artifact(self, file_path: str):
        if file_path not in self.artifacts:
            self.artifacts.append(file_path)

    def finish(
        self,
        status: str,
        final_answer: str,
        assumptions: List[str],
        cache_hits: int = 0,
        cache_misses: int = 0,
        explanation: Optional[str] = None,
    ) -> RunReport:
        total_time = round(time.time() - self.start_time, 2)
        total_cost = round(self.total_cost_inr, 4)

        report = RunReport(
            task=self.task,
            status=status,
            total_steps=len(self.steps),
            total_cost_inr=total_cost,
            total_duration_seconds=total_time,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            verifications=self.verifications,
            artifacts=self.artifacts,
            explanation=explanation,
            final_answer=final_answer,
        )

        # Write report.json
        with open(self.report_file, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))

        # Write result.md
        self._write_result_markdown(status, final_answer, assumptions, total_time, total_cost)

        return report

    def _write_result_markdown(
        self,
        status: str,
        final_answer: str,
        assumptions: List[str],
        total_time: float,
        total_cost: float,
    ):
        lines = [
            f"# Bodhan Task Execution Result",
            f"",
            f"**Task:** {self.task}  ",
            f"**Status:** `{status}`  ",
            f"**Total Cost:** ₹{total_cost:.4f}  ",
            f"**Duration:** {total_time:.2f} seconds  ",
            f"**Steps Taken:** {len(self.steps)}  ",
            f"",
            f"## Final Answer / Summary",
            f"",
            final_answer,
            f"",
        ]

        if assumptions:
            lines.extend([
                f"## Assumptions Made",
                f"",
            ])
            for a in assumptions:
                lines.append(f"- {a}")
            lines.append("")

        if self.artifacts:
            lines.extend([
                f"## Generated Artifacts",
                f"",
            ])
            for art in self.artifacts:
                rel = os.path.relpath(art, self.out_dir)
                lines.append(f"- [{os.path.basename(art)}]({rel})")
            lines.append("")

        if self.verifications:
            lines.extend([
                f"## Verification Checks",
                f"",
                f"| Artifact | Check Type | Result | Details |",
                f"|---|---|---|---|",
            ])
            for v in self.verifications:
                res_str = "✅ Passed" if v.passed else "❌ Failed"
                lines.append(f"| {v.artifact_name} | {v.check_type} | {res_str} | {v.details} |")
            lines.append("")

        lines.extend([
            f"## Execution Trace Overview",
            f"",
            f"| Step | Tool | Plan / Intent | Cost (INR) | Latency |",
            f"|---|---|---|---|---|",
        ])
        for s in self.steps:
            lines.append(f"| {s.step_number} | `{s.tool}` | {s.plan[:60]}... | ₹{s.cost_inr:.4f} | {s.latency_ms:.1f}ms |")

        with open(self.result_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
