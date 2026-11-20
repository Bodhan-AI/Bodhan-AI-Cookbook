"""Generates result.md and report.json output records (SPEC §8.9 and §9)."""

import json
from pathlib import Path
from typing import Any


class ExecutionReporter:
    """Writes standardized output artifacts, result.md, and report.json to out directory."""

    def __init__(self, out_dir: Path | str):
        self.out_dir = Path(out_dir).resolve()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.out_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def write_report_json(
        self,
        status: str,
        inputs: list[dict[str, Any]],
        steps: list[dict[str, Any]],
        verifications: list[dict[str, Any]],
        total_cost_inr: float,
        total_time_s: float,
        assumptions: list[str] | None = None,
        missed_targets: list[str] | None = None,
    ) -> Path:
        """Write out/report.json strictly conforming to SPEC §9 schema."""
        report_data = {
            "status": status,
            "inputs": inputs,
            "steps": steps,
            "verifications": verifications,
            "total_cost_inr": round(total_cost_inr, 4),
            "total_time_s": round(total_time_s, 2),
            "assumptions": assumptions or [],
            "missed_targets": missed_targets or [],
        }

        report_file = self.out_dir / "report.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        return report_file

    def write_result_md(
        self,
        task: str,
        status: str,
        rationale: str,
        final_answer: str,
        artifacts: list[Path],
        assumptions: list[str] | None = None,
    ) -> Path:
        """Write out/result.md summarizing actions, answer, and artifact links."""
        lines = [
            "# Execution Result",
            "",
            f"**Task:** {task}",
            f"**Status:** `{status}`",
            "",
            "## Summary of Actions",
            rationale,
            "",
            "## Final Output",
            final_answer or "*(No content produced)*",
            "",
        ]

        if assumptions:
            lines.extend([
                "## Assumptions & Trade-offs",
                *[f"- {a}" for a in assumptions],
                "",
            ])

        lines.append("## Generated Artifacts")
        if artifacts:
            for art in artifacts:
                rel_path = art.resolve().relative_to(self.out_dir)
                lines.append(f"- [{art.name}]({rel_path})")
        else:
            lines.append("*(No output artifacts generated)*")
        lines.append("")

        result_file = self.out_dir / "result.md"
        with open(result_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return result_file
