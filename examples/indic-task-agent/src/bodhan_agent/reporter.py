"""
Output layout manager and trace logger for Bodhan Task Agent.
Enforces the official take-home output specification:
  out/
    result.md        # what was done, the final answer, assumptions, links to artifacts
    artifacts/       # files produced: audio, subtitles, Markdown, ...
    trace.jsonl      # one line per step: plan, tool, arguments, result summary, latency, cost
    report.json      # status, steps, verification results, total cost and time
"""

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("bodhan.agent.reporter")


class AgentReporter:
    """
    Manages structured output artifacts, real-time step tracing (trace.jsonl),
    and final evaluation reporting (result.md, report.json).
    """

    def __init__(self, out_dir: Union[str, Path] = "./out"):
        self.out_dir = Path(out_dir).resolve()
        self.artifacts_dir = self.out_dir / "artifacts"
        self.trace_file = self.out_dir / "trace.jsonl"
        self.result_file = self.out_dir / "result.md"
        self.report_file = self.out_dir / "report.json"

        # Create output directories
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Initialize or clean trace
        if self.trace_file.exists():
            try:
                self.trace_file.unlink()
            except Exception:
                pass

        self.start_time = time.time()
        self.step_count = 0
        self.total_cost_inr = 0.0
        self.artifacts: List[Dict[str, Any]] = []
        self.verifications: List[Dict[str, Any]] = []
        self.trace_entries: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # 1. Step Tracing (trace.jsonl)
    # -----------------------------------------------------------------------
    def record_step(
        self,
        step_idx: int,
        plan: str,
        tool: str,
        arguments: Dict[str, Any],
        result_summary: str,
        latency_sec: float,
        cost_inr: float,
    ) -> Dict[str, Any]:
        """
        Appends a step entry to trace.jsonl immediately.
        Every step records plan, tool, arguments, result summary, latency, and cost.
        """
        self.step_count = step_idx
        self.total_cost_inr += cost_inr

        # Sanitize arguments: avoid dumping raw byte blobs
        clean_args = {}
        for k, v in arguments.items():
            if isinstance(v, (str, int, float, bool, list, dict)) or v is None:
                clean_args[k] = v
            else:
                clean_args[k] = str(v)

        entry = {
            "step": step_idx,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "plan": plan.strip() if plan else "",
            "tool": tool,
            "arguments": clean_args,
            "result_summary": result_summary.strip() if result_summary else "",
            "latency_seconds": round(latency_sec, 3),
            "cost_inr": round(cost_inr, 5),
            "cumulative_cost_inr": round(self.total_cost_inr, 5),
        }

        self.trace_entries.append(entry)

        # Write to trace.jsonl immediately with flush
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()

        logger.info(f"[Step {step_idx}] Tool: {tool} | Latency: {latency_sec:.2f}s | Cost: ₹{cost_inr:.5f}")
        return entry

    # -----------------------------------------------------------------------
    # 2. Artifacts Management (out/artifacts/)
    # -----------------------------------------------------------------------
    def add_artifact(
        self,
        source_path: Union[str, Path],
        target_name: Optional[str] = None,
        description: str = "",
    ) -> Path:
        """
        Copies a generated file into out/artifacts/ and tracks it.
        Returns the absolute path inside artifacts.
        """
        src = Path(source_path).resolve()
        if not src.exists():
            logger.warning(f"Artifact source file does not exist: {src}")
            return src

        filename = target_name or src.name
        dest = self.artifacts_dir / filename

        if src != dest:
            shutil.copyfile(src, dest)

        rel_path = f"artifacts/{filename}"
        artifact_entry = {
            "filename": filename,
            "relative_path": rel_path,
            "absolute_path": str(dest),
            "size_bytes": dest.stat().st_size,
            "description": description or f"Generated {dest.suffix.lstrip('.')} artifact",
        }
        self.artifacts.append(artifact_entry)
        logger.info(f"Artifact saved: {rel_path} ({dest.stat().st_size} bytes)")
        return dest

    def save_text_artifact(
        self,
        filename: str,
        content: str,
        description: str = "",
    ) -> Path:
        """Writes text/markdown/srt directly into out/artifacts/."""
        dest = self.artifacts_dir / filename
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)

        rel_path = f"artifacts/{filename}"
        artifact_entry = {
            "filename": filename,
            "relative_path": rel_path,
            "absolute_path": str(dest),
            "size_bytes": dest.stat().st_size,
            "description": description or f"Generated {dest.suffix.lstrip('.')} text artifact",
        }
        self.artifacts.append(artifact_entry)
        return dest

    # -----------------------------------------------------------------------
    # 3. Verification Recording
    # -----------------------------------------------------------------------
    def add_verification(
        self,
        check_name: str,
        target_artifact: str,
        passed: bool,
        metric: str,
        score: float,
        details: str = "",
    ):
        """Records self-verification checks (e.g. back-transcription CER/WER)."""
        entry = {
            "check": check_name,
            "target": target_artifact,
            "passed": passed,
            "metric": metric,
            "score": round(score, 4),
            "details": details,
        }
        self.verifications.append(entry)
        logger.info(f"Verification [{check_name}]: Passed={passed} | Score={score:.3f}")

    # -----------------------------------------------------------------------
    # 4. Final Result Writer (out/result.md)
    # -----------------------------------------------------------------------
    def write_result_md(
        self,
        task: str,
        status: str,
        summary: str,
        final_answer: str,
        assumptions: Optional[List[str]] = None,
    ) -> Path:
        """
        Generates the required out/result.md report.
        """
        elapsed_sec = round(time.time() - self.start_time, 2)
        status_badge = {
            "completed": "✅ **COMPLETED**",
            "impossible": "⚠️ **HONEST FAILURE (IMPOSSIBLE TASK)**",
            "budget_exceeded": "🛑 **HALTED (BUDGET EXCEEDED)**",
            "step_limit_exceeded": "🛑 **HALTED (STEP LIMIT REACHED)**",
            "failed": "❌ **FAILED**",
        }.get(status.lower(), f"**{status.upper()}**")

        lines = [
            "# Task Execution Result",
            "",
            f"**Task Prompt:** `{task}`  ",
            f"**Execution Status:** {status_badge}  ",
            f"**Total Steps Taken:** `{self.step_count}`  ",
            f"**Total Cost:** `₹{self.total_cost_inr:.5f}`  ",
            f"**Total Execution Time:** `{elapsed_sec}s`  ",
            "",
            "---",
            "",
            "## Summary of Actions Performed",
            "",
            summary.strip(),
            "",
            "---",
            "",
            "## Final Output & Deliverables",
            "",
            final_answer.strip(),
            "",
        ]

        # Assumptions section (Mandatory take-home spec requirement)
        if assumptions:
            lines.extend([
                "---",
                "",
                "## Assumptions Made",
                "",
            ])
            for a in assumptions:
                lines.append(f"- {a}")
            lines.append("")

        # Artifacts list with relative links
        lines.extend([
            "---",
            "",
            "## Generated Artifacts",
            "",
        ])

        if self.artifacts:
            for art in self.artifacts:
                rel = art["relative_path"]
                fname = art["filename"]
                desc = art["description"]
                size_kb = round(art["size_bytes"] / 1024, 1)
                lines.append(f"- [{fname}]({rel}) ({size_kb} KB) — *{desc}*")
        else:
            lines.append("*No files produced for this task.*")

        lines.append("")

        # Verification section
        if self.verifications:
            lines.extend([
                "---",
                "",
                "## Self-Verification Results",
                "",
                "| Check | Target | Passed | Metric | Score | Details |",
                "|---|---|---|---|---|---|",
            ])
            for v in self.verifications:
                p_str = "Passed" if v["passed"] else "Failed"
                lines.append(f"| {v['check']} | {v['target']} | {p_str} | {v['metric']} | {v['score']} | {v['details']} |")
            lines.append("")

        content = "\n".join(lines)
        with open(self.result_file, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Wrote result to {self.result_file}")
        return self.result_file

    # -----------------------------------------------------------------------
    # 5. Final Report Writer (out/report.json)
    # -----------------------------------------------------------------------
    def write_report_json(
        self,
        status: str,
        task: str,
        error_message: Optional[str] = None,
    ) -> Path:
        """
        Generates out/report.json with execution status, metrics, and verification results.
        """
        total_time_sec = round(time.time() - self.start_time, 2)
        report_data = {
            "status": status,
            "task": task,
            "steps": self.step_count,
            "total_cost_inr": round(self.total_cost_inr, 5),
            "total_time_seconds": total_time_sec,
            "artifacts_count": len(self.artifacts),
            "artifacts": self.artifacts,
            "verification_results": self.verifications,
            "verifications_passed": all(v.get("passed", False) for v in self.verifications) if self.verifications else True,
            "error": error_message,
        }

        with open(self.report_file, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Wrote report to {self.report_file}")
        return self.report_file
