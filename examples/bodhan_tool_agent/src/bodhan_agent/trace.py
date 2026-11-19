"""Execution Tracing and Run Reporting for Bodhan CLI Task Agent.

Generates:
1. trace.jsonl: Per-step record of plan, tool, arguments, summary, latency, and estimated Bodhan cost.
2. report.json: Final machine-readable execution summary.
3. result.md: Human-readable markdown summary linking generated artifacts.
4. runs/: Archived run snapshots (<timestamp>_<task_slug>/) guaranteeing run isolation.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.common.ledger import CostLedger, PRICE_SOURCE_URL, PRICE_VERIFIED_ON


def slugify(text: str, max_length: int = 35) -> str:
    """Convert arbitrary text into a safe, cross-platform filesystem slug."""
    text = text.lower().strip()
    # Replace non-alphanumeric characters (excluding underscores and hyphens) with hyphens
    slug = re.sub(r"[^a-z0-9_-]+", "-", text)
    # Collapse multiple consecutive hyphens
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    if max_length and len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "run"


@dataclass
class StepRecord:
    step: int
    timestamp: str
    plan: str
    tool: str
    arguments: dict[str, Any]
    result_summary: str
    latency_ms: int
    cost_inr: float
    cumulative_cost_inr: float
    status: Literal["success", "error", "budget_exceeded", "halted", "impossible", "failed"]
    cost_basis: str = "not_accounted"
    estimated_cost_inr: float | None = None


class TraceLogger:
    def __init__(self, output_dir: str | Path, task: str = ""):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir = self.output_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.output_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.trace_file = self.output_dir / "trace.jsonl"

        self.task = task
        self.task_slug = slugify(task)

        # Pre-run rotation/archiving of previous run files before starting the new run
        self._archive_previous_run_if_exists()

        # Touch the trace file up front so the output-layout invariant
        # (trace.jsonl + report.json + result.md + artifacts/) holds even for
        # runs that fail before their first logged step.
        self.trace_file.touch(exist_ok=True)
        self.steps: list[StepRecord] = []
        self.start_time = time.time()

        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%d_%H%M%S")
        run_id = f"{ts}_{self.task_slug}"
        if (self.runs_dir / run_id).exists():
            ts = now.strftime("%Y%m%d_%H%M%S_%f")
            run_id = f"{ts}_{self.task_slug}"
        self.timestamp = ts
        self.run_id = run_id
        self.archive_dir: Path | None = None

    def _archive_previous_run_if_exists(self) -> None:
        """Rotate and archive previous run artifacts to out_dir/runs/ if not already archived.

        Guarantees run isolation:
        1. If report.json or trace.jsonl exists in output_dir:
           - Check if already archived in runs/. If not (e.g. crashed or unarchived run),
             archive existing files to runs/<timestamp>_<task_slug>/.
        2. Clean output_dir/artifacts/ so the current run starts with a completely empty directory.
        3. Remove stale report.json and result.md, and reset trace.jsonl for the new run.
        """
        report_file = self.output_dir / "report.json"
        trace_file = self.output_dir / "trace.jsonl"
        result_file = self.output_dir / "result.md"

        has_trace = trace_file.exists() and trace_file.stat().st_size > 0
        has_report = report_file.exists() and report_file.stat().st_size > 0

        if has_trace or has_report:
            already_archived = False
            prev_run_id = None
            prev_task = None
            prev_ts = None

            if has_report:
                try:
                    with open(report_file, "r", encoding="utf-8") as f:
                        prev_report_data = json.load(f)
                    prev_run_id = prev_report_data.get("run_id")
                    prev_task = prev_report_data.get("task")
                    prev_ts = prev_report_data.get("timestamp")
                except Exception:
                    prev_report_data = None

                if prev_run_id:
                    archive_candidate = self.runs_dir / prev_run_id
                    if archive_candidate.is_dir() and (archive_candidate / "report.json").exists():
                        already_archived = True

            if not already_archived:
                # Archive the unarchived or crashed previous run
                if not prev_ts:
                    ref_file = report_file if has_report else trace_file
                    mtime = ref_file.stat().st_mtime
                    prev_ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")

                if not prev_task:
                    if has_trace:
                        try:
                            with open(trace_file, "r", encoding="utf-8") as tf:
                                first_line = tf.readline().strip()
                                if first_line:
                                    rec = json.loads(first_line)
                                    prev_task = rec.get("plan") or "crashed_run"
                        except Exception:
                            prev_task = "crashed_run"
                    else:
                        prev_task = "crashed_run"

                prev_slug = slugify(prev_task or "crashed_run")
                crashed_run_id = f"{prev_ts}_{prev_slug}"
                target_archive = self.runs_dir / crashed_run_id
                if target_archive.exists():
                    target_archive = self.runs_dir / f"{prev_ts}_{int(time.time() * 1000) % 1000:03d}_{prev_slug}"

                target_archive.mkdir(parents=True, exist_ok=True)
                target_artifacts = target_archive / "artifacts"
                target_artifacts.mkdir(parents=True, exist_ok=True)

                for src in (report_file, result_file, trace_file):
                    if src.exists():
                        shutil.copy2(src, target_archive / src.name)

                if self.artifacts_dir.exists():
                    for p in self.artifacts_dir.iterdir():
                        if p.is_file():
                            shutil.copy2(p, target_artifacts / p.name)
                        elif p.is_dir():
                            shutil.copytree(p, target_artifacts / p.name, dirs_exist_ok=True)

        # In all cases where previous files might exist, clean the active output directory
        if self.artifacts_dir.exists():
            for p in self.artifacts_dir.iterdir():
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(p)

        if report_file.exists():
            report_file.unlink()
        if result_file.exists():
            result_file.unlink()
        if trace_file.exists():
            trace_file.unlink()

    def log_step(
        self,
        step: int,
        plan: str,
        tool: str,
        arguments: dict[str, Any],
        result_summary: str,
        latency_ms: int,
        cost_inr: float,
        cumulative_cost_inr: float,
        status: Literal["success", "error", "budget_exceeded", "halted"] = "success",
        cost_basis: str = "not_accounted",
        estimated_cost_inr: float | None = None,
    ) -> StepRecord:
        record = StepRecord(
            step=step,
            timestamp=datetime.now(timezone.utc).isoformat(),
            plan=plan,
            tool=tool,
            arguments=arguments,
            result_summary=result_summary,
            latency_ms=latency_ms,
            cost_inr=cost_inr,
            cumulative_cost_inr=cumulative_cost_inr,
            status=status,
            cost_basis=cost_basis,
            estimated_cost_inr=estimated_cost_inr,
        )
        self.steps.append(record)

        # Immediate flush to trace.jsonl
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")

        return record

    def _mirror_to_runs_archive(self) -> Path:
        """Mirror current run outputs and artifacts to out_dir/runs/<timestamp>_<task_slug>/."""
        target_dir = self.runs_dir / self.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_artifacts = target_dir / "artifacts"
        target_artifacts.mkdir(parents=True, exist_ok=True)

        for filename in ("report.json", "result.md", "trace.jsonl"):
            src_file = self.output_dir / filename
            if src_file.exists():
                shutil.copy2(src_file, target_dir / filename)

        if self.artifacts_dir.exists():
            for p in self.artifacts_dir.iterdir():
                if p.is_file():
                    shutil.copy2(p, target_artifacts / p.name)
                elif p.is_dir():
                    shutil.copytree(p, target_artifacts / p.name, dirs_exist_ok=True)

        self.archive_dir = target_dir
        return target_dir

    def write_report_and_result(
        self,
        task: str,
        status: Literal["completed", "failed", "budget_exhausted", "impossible"],
        final_answer: str,
        cost_ledger: CostLedger,
        verification_details: dict[str, Any] | None = None,
        assumptions: list[str] | None = None,
        plan: dict[str, Any] | None = None,
    ) -> None:
        elapsed_sec = round(time.time() - self.start_time, 2)

        # If task was not known at TraceLogger.__init__, update task, slug, and run_id now
        if not self.task and task:
            self.task = task
            self.task_slug = slugify(task)
            self.run_id = f"{self.timestamp}_{self.task_slug}"
        
        # 1. Write report.json
        artifacts = sorted([
            p for p in self.artifacts_dir.rglob("*")
            if p.is_file()
        ])
        artifacts_list = [p.relative_to(self.artifacts_dir).as_posix() for p in artifacts]
        report_data = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "task": task,
            "status": status,
            "total_steps": len(self.steps),
            "total_cost_inr": cost_ledger.cumulative_cost_inr,
            "estimated_bodhan_cost_inr": cost_ledger.cumulative_cost_inr,
            "max_budget_inr": cost_ledger.max_budget_inr,
            "cost_basis": (
                "Bodhan published unit-rate estimate only; excludes OpenAI-compatible "
                "LLM/gateway charges; translation units are estimated from text length; "
                "nominal rates are applied to cache-served calls; and failed-request charges "
                "are unknown. It is not provider-reported billing."
            ),
            "pricing_source_url": PRICE_SOURCE_URL,
            "pricing_verified_on": PRICE_VERIFIED_ON,
            "elapsed_seconds": elapsed_sec,
            "plan": plan or {},
            "assumptions": assumptions or [],
            "verification": verification_details or {"verified": False, "notes": "None"},
            "artifacts": artifacts_list,
        }
        with open(self.output_dir / "report.json", "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        # 2. Write result.md
        result_lines = [
            "# Task Execution Summary",
            "",
            f"**Task:** {task}  ",
            f"**Status:** `{status.upper()}`  ",
            f"**Total Steps:** {len(self.steps)} | **Estimated Bodhan cost (LLM/gateway excluded):** ₹{cost_ledger.cumulative_cost_inr:.2f} / ₹{cost_ledger.max_budget_inr:.2f} | **Time:** {elapsed_sec}s  ",
            "",
            "The amount above is an estimate based on published Bodhan unit rates, not provider-reported billing. "
            "OpenAI-compatible LLM/gateway charges are excluded. Cache-served calls are counted at nominal rates, "
            "and charges for failed requests may differ; check provider usage records for actual charges.",
        ]

        if plan and (plan.get("goal") or plan.get("steps")):
            result_lines.extend([
                "## Execution Plan",
                "",
            ])
            if plan.get("goal"):
                result_lines.append(f"**Goal:** {plan['goal']}  ")
            if plan.get("target_language"):
                result_lines.append(f"**Target Language:** {plan['target_language']}  ")
            if plan.get("steps"):
                result_lines.append("")
                result_lines.append("**Steps:**")
                for i, s in enumerate(plan["steps"], 1):
                    result_lines.append(f"{i}. {s}")
            result_lines.append("")

        if assumptions:
            result_lines.extend([
                "## Assumptions Made",
                "",
                *[f"- {a}" for a in assumptions],
                "",
            ])

        result_lines.extend([
            "## Final Output",
            "",
            final_answer.strip(),
            "",
        ])

        if artifacts:
            result_lines.extend([
                "## Generated Artifacts",
                "",
                *[
                    f"- [`{p.relative_to(self.artifacts_dir).as_posix()}`](artifacts/{p.relative_to(self.artifacts_dir).as_posix()}) ({p.stat().st_size / 1024:.1f} KB)"
                    for p in artifacts
                ],
                "",
            ])

        with open(self.output_dir / "result.md", "w", encoding="utf-8") as f:
            f.write("\n".join(result_lines))

        # 3. Mirror current run to runs archive snapshot
        self._mirror_to_runs_archive()
