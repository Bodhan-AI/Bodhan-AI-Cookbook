"""
Tracer — records every step of the agent's execution for replay and debugging.

Writes trace.jsonl (one JSON line per step) and generates report.json summary.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TraceEntry:
    """A single trace entry."""
    step: int
    type: str  # 'plan', 'tool_call', 'verification', 'error', 'summary'
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    result_summary: str = ""
    latency_seconds: float = 0.0
    cost_inr: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "step": self.step,
            "type": self.type,
            "timestamp": self.timestamp,
            "latency_seconds": round(self.latency_seconds, 3),
            "cost_inr": round(self.cost_inr, 4),
        }
        if self.tool:
            d["tool"] = self.tool
        if self.arguments:
            # Sanitize: truncate long values, remove binary data
            d["arguments"] = _sanitize_args(self.arguments)
        if self.result_summary:
            d["result_summary"] = self.result_summary[:500]  # Truncate
        if self.metadata:
            d["metadata"] = self.metadata
        return d


class Tracer:
    """
    Records the agent's execution trace.

    Writes each step as a JSON line to trace.jsonl in real time.
    """

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._trace_path = output_dir / "trace.jsonl"
        self._entries: list[TraceEntry] = []
        self._step_counter = 0
        self._start_time = time.time()

        # Clear existing trace file
        self._trace_path.write_text("", encoding="utf-8")

    def record(
        self,
        type: str,
        tool: str | None = None,
        arguments: dict[str, Any] | None = None,
        result_summary: str = "",
        latency_seconds: float = 0.0,
        cost_inr: float = 0.0,
        **metadata: Any,
    ) -> TraceEntry:
        """Record a trace entry and write it immediately."""
        self._step_counter += 1
        entry = TraceEntry(
            step=self._step_counter,
            type=type,
            tool=tool,
            arguments=arguments,
            result_summary=result_summary,
            latency_seconds=latency_seconds,
            cost_inr=cost_inr,
            metadata=metadata,
        )
        self._entries.append(entry)

        # Append to trace file immediately
        with open(self._trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        return entry

    def record_plan(self, plan_text: str) -> TraceEntry:
        """Record the agent's plan."""
        return self.record(
            type="plan",
            result_summary=plan_text,
        )

    def record_tool_call(
        self,
        tool: str,
        arguments: dict[str, Any],
        result_summary: str,
        latency_seconds: float,
        cost_inr: float = 0.0,
    ) -> TraceEntry:
        """Record a tool call and its result."""
        return self.record(
            type="tool_call",
            tool=tool,
            arguments=arguments,
            result_summary=result_summary,
            latency_seconds=latency_seconds,
            cost_inr=cost_inr,
        )

    def record_verification(self, check: str, passed: bool, details: str = "") -> TraceEntry:
        """Record a verification step."""
        return self.record(
            type="verification",
            result_summary=f"{'PASS' if passed else 'FAIL'}: {check} — {details}",
            check=check,
            passed=passed,
        )

    def record_error(self, error: str, recoverable: bool = True) -> TraceEntry:
        """Record an error."""
        return self.record(
            type="error",
            result_summary=error,
            recoverable=recoverable,
        )

    def generate_report(
        self,
        status: str,
        task: str,
        budget_summary: dict[str, Any],
        verification_results: list[dict[str, Any]] | None = None,
        assumptions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate the final report.json."""
        elapsed = time.time() - self._start_time
        report = {
            "status": status,
            "task": task,
            "total_steps": self._step_counter,
            "total_time_seconds": round(elapsed, 2),
            "budget": budget_summary,
            "verification_results": verification_results or [],
            "assumptions": assumptions or [],
            "trace_file": str(self._trace_path),
        }

        report_path = self._output_dir / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"Report written to {report_path}")
        return report

    @property
    def entries(self) -> list[TraceEntry]:
        return self._entries


def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Remove/truncate sensitive or large values from arguments for the trace."""
    sanitized = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            sanitized[k] = v[:200] + f"... ({len(v)} chars total)"
        elif isinstance(v, bytes):
            sanitized[k] = f"<binary {len(v)} bytes>"
        else:
            sanitized[k] = v
    return sanitized
