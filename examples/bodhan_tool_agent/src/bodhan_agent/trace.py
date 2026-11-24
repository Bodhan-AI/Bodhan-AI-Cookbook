"""JSONL trace recorder for reproducible execution audit trails (SPEC §8.8)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TraceLogger:
    """Manages monotonically increasing step-by-step trace logging to trace.jsonl."""

    def __init__(self, trace_file: Path | str):
        self.trace_path = Path(trace_file)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self.step_counter = 0

    def _append(self, record_type: str, data: dict[str, Any]) -> None:
        self.step_counter += 1
        entry = {
            "step": self.step_counter,
            "type": record_type,
            "ts": datetime.now(UTC).isoformat(),
            **data,
        }
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_resolve(self, inventory: list[dict[str, Any]], selected: list[str], rationale: str) -> None:
        self._append("resolve", {"inventory": inventory, "selected": selected, "rationale": rationale})

    def record_preflight(self, estimated_cost_inr: float, max_cost_inr: float, affordable: bool) -> None:
        self._append(
            "preflight",
            {
                "estimated_cost_inr": estimated_cost_inr,
                "max_cost_inr": max_cost_inr,
                "affordable": affordable,
            },
        )

    def record_plan(self, plan: dict[str, Any], rationale: str) -> None:
        self._append("plan", {"plan": plan, "rationale": rationale})

    def record_tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        result_summary: str,
        latency_ms: float,
        cost_inr: float,
        error: str | None = None,
    ) -> None:
        self._append(
            "tool_call",
            {
                "tool": tool,
                "args": args,
                "result_summary": result_summary,
                "latency_ms": round(latency_ms, 2),
                "cost_inr": round(cost_inr, 4),
                "error": error,
            },
        )

    def record_verification(self, check: str, metric: Any, passed: bool, details: dict[str, Any] | None = None) -> None:
        self._append(
            "verification",
            {
                "check": check,
                "metric": metric,
                "passed": passed,
                "details": details or {},
            },
        )

    def record_security(self, event: str, pattern: str, snippet: str) -> None:
        self._append(
            "security",
            {
                "event": event,
                "pattern": pattern,
                "snippet": snippet,
            },
        )

    def record_error(self, code: str, message: str, will_retry: bool = False) -> None:
        self._append("error", {"code": code, "message": message, "will_retry": will_retry})

    def record_final(self, status: str, total_cost_inr: float, total_time_s: float) -> None:
        self._append(
            "final",
            {
                "status": status,
                "total_cost_inr": round(total_cost_inr, 4),
                "total_time_s": round(total_time_s, 2),
            },
        )
