"""Audit tracer and report generator.
Writes trace.jsonl, report.json, and result.md into out/.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from bodhan_agent.verifier import VerificationResult


class Tracer:
    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir).resolve()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.trace_file = self.out_dir / "trace.jsonl"
        if self.trace_file.exists():
            try:
                self.trace_file.unlink()
            except OSError:
                pass
        self.report_file = self.out_dir / "report.json"
        self.result_file = self.out_dir / "result.md"

        self.start_time = time.time()
        self.steps: List[Dict[str, Any]] = []
        self.assumptions: List[str] = []
        self.verification_results: List[Dict[str, Any]] = []
        self.artifacts_created: List[str] = []

    def to_rel_path(self, path_str: str) -> str:
        """Convert any path to a clean relative path to self.out_dir if inside it."""
        try:
            p = Path(path_str).resolve()
            if p == self.out_dir:
                return "."
            if self.out_dir in p.parents:
                return str(p.relative_to(self.out_dir)).replace("\\", "/")
        except Exception:
            pass
        # Replace occurrences of out_dir path string if embedded
        s = str(path_str)
        out_str = str(self.out_dir)
        if out_str in s:
            s = s.replace(out_str + "\\", "").replace(out_str + "/", "").replace(out_str, ".")
        return s.replace("\\", "/")

    def _sanitize_data(self, val: Any) -> Any:
        """Recursively sanitize data structures to convert out_dir absolute paths to relative."""
        if isinstance(val, (str, Path)):
            return self.to_rel_path(str(val))
        elif isinstance(val, dict):
            return {k: self._sanitize_data(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [self._sanitize_data(v) for v in val]
        return val

    def record_step(
        self,
        step_number: int,
        plan_step: str,
        tool: str,
        arguments: Dict[str, Any],
        result_summary: str,
        latency_ms: int,
        cost_inr: float,
        cumulative_cost_inr: float,
        status: str = "success",
    ) -> None:
        """Append step trace to trace.jsonl with relative paths."""
        entry = {
            "step": step_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plan_step": plan_step,
            "tool": tool,
            "arguments": self._sanitize_data(arguments),
            "result_summary": self.to_rel_path(result_summary),
            "latency_ms": latency_ms,
            "cost_inr": round(cost_inr, 4),
            "cumulative_cost_inr": round(cumulative_cost_inr, 4),
            "status": status,
        }
        self.steps.append(entry)

        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def add_assumption(self, assumption: str) -> None:
        self.assumptions.append(assumption)

    def add_artifact(self, artifact_path: str) -> None:
        rel = self.to_rel_path(artifact_path)
        if rel not in self.artifacts_created:
            self.artifacts_created.append(rel)

    def record_verifications(self, results: List[VerificationResult]) -> None:
        for r in results:
            d = r.to_dict()
            d["artifact"] = self.to_rel_path(d["artifact"])
            self.verification_results.append(d)

    def finalize(
        self,
        task: str,
        status: str,
        total_cost_inr: float,
        error_message: Optional[str] = None,
        final_answer: str = "",
        llm_calls: int = 0,
        llm_tokens: Optional[dict] = None,
        budget_limit_inr: Optional[float] = None,
        user_stated_budget_inr: Optional[float] = None,
    ) -> None:
        """Write report.json and result.md using relative paths only."""
        total_duration = round(time.time() - self.start_time, 2)

        # 1. Write report.json
        report_data = {
            "status": status,
            "task": task,
            "total_steps": len(self.steps),
            "total_cost_inr": round(total_cost_inr, 4),
            "effective_budget_cap_inr": round(budget_limit_inr, 4) if budget_limit_inr is not None else None,
            "user_stated_budget_inr": round(user_stated_budget_inr, 4) if user_stated_budget_inr is not None else None,
            "duration_seconds": total_duration,
            "llm_calls": llm_calls,
            "tokens": llm_tokens or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "llm_tokens": llm_tokens or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "verification_results": self.verification_results,
            "artifacts": self.artifacts_created,
            "assumptions": self.assumptions,
            "error": error_message,
        }
        with open(self.report_file, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        # 2. Write result.md
        budget_summary = f"- **Total Cost**: ₹{total_cost_inr:.4f}"
        if budget_limit_inr is not None:
            budget_summary += f" (Effective Hard Cap: ₹{budget_limit_inr:.4f}"
            if user_stated_budget_inr is not None:
                budget_summary += " [user-stated])"
            else:
                budget_summary += ")"

        md_lines = [
            f"# Task Execution Result: {status.upper()}",
            "",
            "## Summary",
            f"**Task**: {task}",
            f"- **Status**: `{status}`",
            f"- **Total Time**: {total_duration}s",
            budget_summary,
            f"- **Steps Executed**: {len(self.steps)}",
            "",
        ]

        if error_message:
            md_lines.extend([
                "## Error / Limitations",
                f"> {error_message}",
                "",
            ])

        if self.assumptions:
            md_lines.extend([
                "## Assumptions Made",
            ] + [f"- {a}" for a in self.assumptions] + [""])

        clean_final_answer = self.to_rel_path(final_answer) if final_answer else "Artifacts generated successfully:"
        md_lines.extend([
            "## Final Answer & Artifacts",
            clean_final_answer,
            "",
        ])

        if self.artifacts_created:
            for art in self.artifacts_created:
                md_lines.append(f"- [{art}]({art})")
            md_lines.append("")

        if self.verification_results:
            md_lines.extend([
                "## Verification Suite",
                "| Artifact | Check | Status | Details |",
                "| :--- | :--- | :--- | :--- |",
            ])
            has_weak = False
            for v in self.verification_results:
                badge = v.get("status") or ("PASS" if v["passed"] else "FAIL")
                if badge == "WEAK":
                    has_weak = True
                md_lines.append(f"| `{v['artifact']}` | `{v['check']}` | **{badge}** | {v['details']} |")
            md_lines.append("")

            if has_weak:
                md_lines.extend([
                    "### Verification Warning",
                    "> **Quality Warning**: Verification flagged one or more checks as **WEAK** (e.g. back-transcription speech coverage below 70%). Output quality may be degraded.",
                    "",
                ])

        with open(self.result_file, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines).strip() + "\n")

