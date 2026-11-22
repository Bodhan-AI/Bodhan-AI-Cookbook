"""Tracer and artifact manager for task agent execution."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bodhan_agent.models import AgentReport, StepTrace

KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_.-]{16,}", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Removes API keys or authorization bearer tokens from logged text."""
    redacted = text
    for pat in KEY_PATTERNS:
        redacted = pat.sub("[REDACTED_KEY]", redacted)
    return redacted


class AgentTracer:
    """Handles logging of trace.jsonl, report.json, and result.md into the output directory."""

    def __init__(self, out_dir: Path | str):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.out_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.trace_file = self.out_dir / "trace.jsonl"
        self.report_file = self.out_dir / "report.json"
        self.result_file = self.out_dir / "result.md"

        self.traces: list[StepTrace] = []
        self.reset()

    def reset(self) -> None:
        """Starts fresh by clearing in-memory traces and removing previous trace.jsonl, report.json, result.md."""
        self.traces.clear()
        for f in (self.trace_file, self.report_file, self.result_file):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass

    def record_step(self, trace: StepTrace) -> None:
        """Records a step to memory and appends to trace.jsonl atomically."""
        self.traces.append(trace)
        clean_dict = json.loads(redact_secrets(json.dumps(trace.to_dict(), ensure_ascii=False)))
        line = json.dumps(clean_dict, ensure_ascii=False) + "\n"

        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(line)

    def write_report(self, report: AgentReport) -> None:
        """Writes report.json with full execution summary."""
        clean_dict = json.loads(redact_secrets(json.dumps(report.to_dict(), ensure_ascii=False)))
        self.report_file.write_text(json.dumps(clean_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_result_markdown(
        self,
        task: str,
        status: str,
        final_answer: str,
        assumptions: list[str],
        artifacts: list[str],
        failures: list[str],
    ) -> None:
        """Generates result.md documenting the run, answers, assumptions, and links to artifacts."""
        lines = [
            f"# Task Execution Result: {status.upper()}",
            "",
            f"**Task Prompt**: {task}",
            f"**Generated At**: {datetime.now(timezone.utc).isoformat()}",
            "",
        ]

        if failures:
            lines.extend([
                "## Failed Steps",
                "",
                "\n".join(f"- {f}" for f in failures),
                "",
            ])

        lines.extend([
            "## Summary of Actions & Answer",
            "",
            final_answer.strip() if final_answer.strip() else "Task completed.",
            "",
        ])

        if assumptions:
            lines.extend([
                "## Assumptions Made",
                "",
                "\n".join(f"- {a}" for a in assumptions),
                "",
            ])

        if artifacts:
            lines.extend([
                "## Produced Artifacts",
                "",
                "\n".join(f"- [{Path(a).name}](artifacts/{Path(a).name})" for a in artifacts),
                "",
            ])

        content = redact_secrets("\n".join(lines))
        self.result_file.write_text(content, encoding="utf-8")
