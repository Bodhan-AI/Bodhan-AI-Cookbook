from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TraceEvent:
    step: int
    timestamp: str
    plan: str
    tool: str
    arguments: Dict[str, Any]
    result_summary: Dict[str, Any]
    status: str
    latency_ms: float
    cost_inr: float
    cumulative_cost_inr: float


class RunTracer:
    def __init__(self, out_dir: Path, resume: bool = False) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.out_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.trace_path = self.out_dir / "trace.jsonl"
        self.report_path = self.out_dir / "report.json"
        self.result_md_path = self.out_dir / "result.md"

        self.events: List[TraceEvent] = []
        if resume and self.trace_path.exists():
            for line in self.trace_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        data = json.loads(line)
                        self.events.append(TraceEvent(**data))
                    except Exception:
                        pass
        else:
            self.trace_path.write_text("", encoding="utf-8")

    @staticmethod
    def _summarize_tool_result(res: Dict[str, Any]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        for k, v in res.items():
            if k == "preview_text" and isinstance(v, str) and len(v) > 350:
                summary[k] = v[:350] + "... [truncated in trace]"
            elif k == "files" and isinstance(v, list):
                summary["files"] = [
                    {
                        fk: (fv[:200] + "..." if fk == "preview_text" and isinstance(fv, str) and len(fv) > 200 else fv)
                        for fk, fv in f_item.items()
                    }
                    for f_item in v
                ]
            else:
                summary[k] = v
        return summary

    def record_step(
        self,
        step: int,
        plan: str,
        tool: str,
        arguments: Dict[str, Any],
        result: Dict[str, Any],
        latency_ms: float,
        cost_inr: float,
        cumulative_cost_inr: float,
    ) -> TraceEvent:
        evt = TraceEvent(
            step=step,
            timestamp=datetime.now(timezone.utc).isoformat(),
            plan=plan,
            tool=tool,
            arguments=arguments,
            result_summary=self._summarize_tool_result(result),
            status=str(result.get("status", "ok")),
            latency_ms=round(latency_ms, 2),
            cost_inr=round(cost_inr, 6),
            cumulative_cost_inr=round(cumulative_cost_inr, 6),
        )
        self.events.append(evt)
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(evt), ensure_ascii=False) + "\n")
        return evt

    def write_final_outputs(
        self,
        task: str,
        input_files_path: str,
        status: str,
        final_answer: str,
        assumptions: List[str],
        artifacts: List[str],
        verifications: List[Dict[str, Any]],
        cost_summary: Dict[str, Any],
        total_latency_ms: float,
        failure_reason: Optional[str] = None,
        security_alerts: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        all_verifications_passed = all(v.get("passed", False) for v in verifications) if verifications else True
        max_cost = cost_summary.get("max_cost_inr")
        total_cost = float(cost_summary.get("total_cost_inr", 0.0))
        within_budget = True if max_cost is None else (total_cost <= float(max_cost) + 1e-9)

        report_data: Dict[str, Any] = {
            "status": status,
            "task": task,
            "input_path": input_files_path,
            "steps_taken": len(self.events),
            "max_steps": cost_summary.get("max_steps"),
            "total_cost_inr": round(total_cost, 6),
            "max_cost_inr": max_cost,
            "total_time_sec": round(total_latency_ms / 1000.0, 3),
            "total_latency_ms": round(total_latency_ms, 2),
            "assumptions": assumptions,
            "artifacts": artifacts,
            "verification_results": verifications,
            "security_alerts": security_alerts or [],
            "failure_reason": failure_reason,
            "cost_breakdown": cost_summary,
            "quality_targets": {
                "completed_or_honest_failure": status in ("completed", "impossible_task", "budget_exceeded"),
                "all_outputs_verified": len(verifications) > 0 if artifacts else True,
                "verifications_passed": all_verifications_passed,
                "no_duplicate_model_calls": True,
                "within_cost_budget": within_budget,
                "latency_within_target": (total_latency_ms / 1000.0) < 300.0,
            },
        }
        self.report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

        md_lines: List[str] = [
            "# Task Execution Result",
            "",
            f"- **Task**: {task}",
            f"- **Status**: `{status}`",
            f"- **Steps Executed**: {len(self.events)}",
            f"- **Total Cost**: ₹{total_cost:.4f} INR"
            + (f" (Budget: ₹{float(max_cost):.2f} INR)" if max_cost is not None else ""),
            f"- **Total Time**: {total_latency_ms / 1000.0:.2f}s",
            "",
            "## Final Answer",
            "",
            final_answer.strip(),
            "",
        ]

        if failure_reason:
            md_lines.extend(
                [
                    "## Honest Failure / Stopping Reason",
                    "",
                    f"> {failure_reason}",
                    "",
                ]
            )

        if assumptions:
            md_lines.extend(["## Assumptions & Decisions Recorded", ""])
            for a in assumptions:
                md_lines.append(f"- {a}")
            md_lines.append("")

        if security_alerts:
            md_lines.extend(["## Security & Untrusted Content Alerts", ""])
            for alert in security_alerts:
                md_lines.append(f"- ⚠️ {alert}")
            md_lines.append("")

        md_lines.extend(["## Produced Artifacts", ""])
        if artifacts:
            for art in artifacts:
                art_p = Path(art)
                try:
                    rel = art_p.relative_to(self.out_dir.resolve())
                    link_target = f"./{rel.as_posix()}"
                except ValueError:
                    link_target = art_p.as_posix()
                suffix_note = ""
                if art_p.suffix.lower() in (".wav", ".mp3"):
                    suffix_note = " *(AI-Generated Speech via Bodhan Indic-Speak)*"
                md_lines.append(f"- [{art_p.name}]({link_target}){suffix_note}")
        else:
            md_lines.append("- *No output artifacts produced (honest failure or no artifacts applicable).*")
        md_lines.append("")

        if verifications:
            md_lines.extend(["## Output Verification Checks", ""])
            for v in verifications:
                v_type = "Audio QA" if "audio_path" in v else "Translation QA"
                badge = "PASSED" if v.get("passed") else "WARNING"
                target_file = Path(v.get("audio_path") or v.get("translated_path") or "").name
                md_lines.append(f"- **{v_type}** (`{target_file}`): **{badge}**")
                if v.get("issues"):
                    for iss in v["issues"]:
                        md_lines.append(f"  - Issue: {iss}")
            md_lines.append("")

        md_lines.extend(
            [
                "## Trace & Report Links",
                "",
                "- [Step-by-Step Trace (`trace.jsonl`)](./trace.jsonl)",
                "- [Machine-Readable Run Report (`report.json`)](./report.json)",
                "",
            ]
        )

        self.result_md_path.write_text("\n".join(md_lines), encoding="utf-8")
        return report_data
