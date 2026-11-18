
import json
from datetime import datetime, timezone
from pathlib import Path


class ReportGenerator:
    """Generate JSON and Markdown reports for an agent run."""

    def __init__(self, output_dir="out"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def generate(self, task, run_result):
        """Write report.json and result.md."""

        history = run_result.get("history", [])
        status = run_result.get("status", "unknown")

        report = {
            "task": task,
            "status": status,
            "generated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "executed_tools": [
                item["tool"]
                for item in history
            ],
            "completed_steps": len(history),
            "budget": run_result.get("budget", {}),
            "cost": run_result.get("cost", {}),
            "trace_path": run_result.get(
                "trace_path"
            ),
            "steps": history,
        }

        report_path = (
            self.output_dir / "report.json"
        )

        report_path.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        lines = [
            "# OmniAgent AI — Task Result",
            "",
            "## Task",
            "",
            task,
            "",
            "## Execution Status",
            "",
            f"**{status.upper()}**",
            "",
            "## Workflow",
            "",
        ]

        if not history:
            lines.append("No tools were executed.")
        else:
            for index, item in enumerate(
                history, start=1
            ):
                lines.extend([
                    f"### Step {index}: {item['tool']}",
                    "",
                    "```json",
                    json.dumps(
                        item.get("result", {}),
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                    "```",
                    "",
                ])

        lines.extend([
            "## Execution Budget",
            "",
            "```json",
            json.dumps(
                report["budget"],
                indent=2,
            ),
            "```",
            "",
            "## Cost Tracking",
            "",
            "```json",
            json.dumps(
                report["cost"],
                indent=2,
            ),
            "```",
            "",
        ])

        if not report["cost"].get(
            "cost_complete", False
        ):
            lines.extend([
                "**Note:** Actual API cost is unknown "
                "because complete pricing information "
                "is unavailable.",
                "",
            ])

        lines.extend([
            "## Trace",
            "",
            f"`{report['trace_path'] or 'Not available'}`",
            "",
        ])

        result_path = (
            self.output_dir / "result.md"
        )

        result_path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        return {
            "report_path": str(report_path),
            "result_path": str(result_path),
        }
